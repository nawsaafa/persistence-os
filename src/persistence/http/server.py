"""FastAPI app construction (Phase 2.1c, Design §3, §4)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from persistence.http.blob_store import BlobStore
from persistence.http.routes.blob import router as blob_router
from persistence.http.routes.claim import router as claim_router
from persistence.sdk import Substrate


def build_app() -> FastAPI:
    app = FastAPI(title="persistence-os HTTP surface", version="2.1c")

    # Server startup posture (Design §9): refuse to start if API key unset AND bypass off.
    api_key = os.environ.get("PERSISTENCE_API_KEY")
    bypass = os.environ.get("PERSISTENCE_HTTP_LOOPBACK_BYPASS") == "1"
    if api_key is None and not bypass:
        raise SystemExit(
            "persistence-os HTTP refuses to start: PERSISTENCE_API_KEY is unset AND "
            "PERSISTENCE_HTTP_LOOPBACK_BYPASS is not '1'. This combination would silently "
            "accept unauthenticated requests. Set one or both."
        )

    # Blob store available to routes via app state.
    blob_root = Path(os.environ.get("PERSISTENCE_BLOB_ROOT", "blobs"))
    app.state.blob_store = BlobStore(root=blob_root)

    # Substrate wiring: Substrate.open() is a classmethod that returns a
    # Substrate instance directly (not a context manager). __enter__ simply
    # returns self, so direct instantiation is safe and idiomatic for a
    # long-lived FastAPI app state. The substrate is never explicitly closed
    # (app teardown = process exit). Routes access it via
    # request.app.state.substrate.
    persistence_root = os.environ.get("PERSISTENCE_ROOT", "memory")
    app.state.substrate = Substrate.open(persistence_root)

    # Uniform error envelope for HTTPException (Design §4 R1.2 N1 closure).
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": "unknown", "detail": str(exc.detail)},
        )

    # Map RequestValidationError (Pydantic at the request boundary) →
    # 422 attrs_validation_failed OR 400 malformed_body — depending on whether
    # the body parsed at all.
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        try:
            errors = exc.errors()
            looks_attrs = any(e.get("loc", (None,))[0] == "body" for e in errors)
        except Exception:
            looks_attrs = False
        if looks_attrs:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "attrs_validation_failed",
                    "detail": json.dumps(exc.errors()),
                },
            )
        return JSONResponse(
            status_code=400,
            content={"error": "malformed_body", "detail": json.dumps(exc.errors())},
        )

    app.include_router(claim_router)
    app.include_router(blob_router)
    return app
