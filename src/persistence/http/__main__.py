"""Uvicorn entrypoint (Phase 2.1c, Design §7.1 N3 runtime-pin).

`python -m persistence.http` starts the HTTP server with proxy_headers=False
and forwarded_allow_ips="" so the ASGI runtime cannot rewrite
`request.client.host` from headers. This is part of the security contract,
not a deployment hint — see test_main_smoke.py for the assertion.
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    # Forced spec deviation: spec signature uses `argv=None` → `parse_args(argv)`.
    # When argv is None, argparse reads sys.argv[1:], which in test context
    # contains pytest CLI arguments that argparse rejects. We resolve by
    # defaulting to [] (empty list) so `main()` with no args is a no-op parse.
    # CLI entry-point (`if __name__ == "__main__"`) passes sys.argv[1:] explicitly.
    if argv is None:
        argv = []
    parser = argparse.ArgumentParser(
        prog="persistence.http",
        description="persistence-os HTTP surface (Phase 2.1c context substrate)",
    )
    parser.add_argument("--host", default=os.environ.get("PERSISTENCE_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PERSISTENCE_HTTP_PORT", "47823")))
    args = parser.parse_args(argv)

    # Lazy import: friendly RuntimeError if [http] extras not installed
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "persistence.http requires `pip install persistence[http]` (fastapi, uvicorn, pydantic). "
            "Bare install is intentional — HTTP surface is opt-in."
        ) from e

    from persistence.http.server import build_app
    app = build_app()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        # Load-bearing for B2 closure — see Design §7.1, test_main_smoke.py
        proxy_headers=False,
        forwarded_allow_ips="",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
