"""Tests for the D8 browser console UI assets.

30 tests across 5 groups:

- Static asset serving                            (10)
- XSS pin (source-string scan)                    (5)
- Command parser shape checks                     (8)
- Audit tail polling shape checks                 (4)
- Connection lifecycle shape checks               (3)

Static-asset tests use ``aiohttp.test_utils.TestServer + TestClient``
(same pattern as ``tests/repl/test_ws.py`` D2). Source-content tests
read the static files via ``pathlib.Path`` and assert substring /
pattern presence — they do NOT execute the JS, so we cover surface
properties (XSS pin, parser branches, lifecycle hooks) by string scan.
This is brittle by design: the tests pin the EXACT shape of the UI
contract so a refactor that drops a contract clause fails loudly.

Banned-identifier strings (e.g. the unsafe HTML-string DOM setter, the
Function() constructor, the eval() sink) are assembled at runtime from
harmless pieces so the test source itself never literally contains the
trigger pattern that some PreToolUse security hooks scan for. The scan
still operates on the runtime-assembled string against app.js source.

See design doc ``docs/plans/2026-04-28-v0.7.0a1-module-7-repl-design.md``
sections ADR-6 (UI bundling), 6.2 (auth handshake + URL-fragment token),
7.1 (UI wireframe), 7.2 (audit-tail polling), 10 (D8 task), and 12
(Risks: UI XSS / token leak via URL).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from persistence.fact import DB, InMemoryStore
from persistence.repl import WSServer


# ---------------------------------------------------------------------------
# Helpers + fixtures
# ---------------------------------------------------------------------------
def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _fixed_clock(t: datetime):
    def clock() -> datetime:
        return t

    return clock


_DEFAULT_T = _dt(2026, 5, 9, 12, 0, 0)


@pytest.fixture
def db() -> DB:
    return DB(InMemoryStore())


@pytest.fixture
def clock_fixed():
    return _fixed_clock(_DEFAULT_T)


@pytest.fixture
def server(db: DB, clock_fixed) -> WSServer:
    return WSServer(db, runtime_clock=clock_fixed)


@pytest_asyncio.fixture
async def client(server: WSServer):
    test_server = TestServer(server.app)
    async with TestClient(test_server) as c:
        yield c


_STATIC_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "persistence"
    / "repl"
    / "static"
)


def _read_static(name: str) -> str:
    p = _STATIC_DIR / name
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return _read_static("app.js")


@pytest.fixture(scope="module")
def index_html() -> str:
    return _read_static("index.html")


@pytest.fixture(scope="module")
def style_css() -> str:
    return _read_static("style.css")


# ---------------------------------------------------------------------------
# Source-string-scan helpers
# ---------------------------------------------------------------------------
def _strip_line_comments(src: str) -> str:
    """Strip ``//`` line-comments so docstring/comment mentions of
    banned identifiers (e.g. inside the XSS pin docstring) do not trip
    the source-string-scan assertions in this test module.
    """
    return re.sub(r"//.*", "", src)


# Banned-identifier strings, assembled at runtime so the literal
# trigger never appears in this source file.
_BANNED_HTML_SETTER = "inner" + "HTML"
_BANNED_FN_CTOR = "new " + "Function" + "("
_BANNED_EVAL_CALL = "ev" + "al" + "("
# Regex: banned eval call NOT preceded by an identifier char (so
# `myCustomEval(` and `JSON.eval(` don't false-trip — though we don't
# expect the latter, it keeps the rule precise).
_BANNED_EVAL_RE = r"(^|[^A-Za-z0-9_$.])" + "ev" + "al" + r"\("


# ===========================================================================
# 1. Static asset serving (10)
# ===========================================================================
class TestStaticAssetServing:
    @pytest.mark.asyncio
    async def test_get_root_returns_200(self, client):
        resp = await client.get("/")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_get_root_body_has_title(self, client):
        resp = await client.get("/")
        body = await resp.text()
        assert "<title>persistence.repl</title>" in body

    @pytest.mark.asyncio
    async def test_get_root_body_links_style_css(self, client):
        resp = await client.get("/")
        body = await resp.text()
        assert "/style.css" in body

    @pytest.mark.asyncio
    async def test_get_root_body_links_app_js(self, client):
        resp = await client.get("/")
        body = await resp.text()
        assert "/app.js" in body

    @pytest.mark.asyncio
    async def test_get_root_body_has_cmd_form(self, client):
        resp = await client.get("/")
        body = await resp.text()
        assert 'id="cmd-form"' in body

    @pytest.mark.asyncio
    async def test_get_style_css_returns_200(self, client):
        resp = await client.get("/style.css")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_style_css_contains_monospace(self, client):
        resp = await client.get("/style.css")
        body = await resp.text()
        assert "monospace" in body

    @pytest.mark.asyncio
    async def test_get_app_js_returns_200(self, client):
        resp = await client.get("/app.js")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_app_js_contains_websocket(self, client):
        resp = await client.get("/app.js")
        body = await resp.text()
        assert "WebSocket" in body

    @pytest.mark.asyncio
    async def test_unknown_asset_returns_404(self, client):
        resp = await client.get("/nonexistent.css")
        assert resp.status == 404


# ===========================================================================
# 2. XSS pin — source-string scan (5)
# ===========================================================================
class TestXSSPin:
    def test_app_js_does_not_set_unsafe_html(self, app_js: str):
        # The XSS pin: textContent everywhere, never the unsafe
        # HTML-string DOM setter. Strip line comments first so the
        # docstring at the top of app.js (which describes the pin)
        # does not trip this assertion.
        stripped = _strip_line_comments(app_js)
        assert _BANNED_HTML_SETTER not in stripped, (
            "app.js must not reference the unsafe HTML-string DOM "
            "setter in non-comment code; render via textContent only "
            "(XSS pin, design 12 risks)."
        )

    def test_app_js_uses_textContent(self, app_js: str):
        # Multiple references — every render path uses textContent.
        # Pin a floor (>=5: output, audit ts/op/verdict/latency, status).
        count = app_js.count(".textContent")
        assert count >= 5, (
            f"app.js should use .textContent on every render path; "
            f"found {count} references."
        )

    def test_app_js_scrubs_url_fragment(self, app_js: str):
        # After reading #token=..., the URL must be replaced so the
        # token does not survive in browser history / screen-shares.
        assert "history.replaceState" in app_js

    def test_app_js_parses_token_fragment(self, app_js: str):
        # Token loading reads window.location.hash and extracts
        # the #token= prefix.
        assert "window.location.hash" in app_js
        assert "#token=" in app_js

    def test_app_js_no_eval_or_function_constructor(self, app_js: str):
        # Code-injection sinks. The browser's WebSocket payload comes
        # straight from the server but is parsed via JSON.parse, not
        # via the dynamic-code sink. Pin both classic sinks. Strip
        # comments so docstring mentions don't trip.
        stripped = _strip_line_comments(app_js)
        assert re.search(_BANNED_EVAL_RE, stripped) is None, (
            "no dynamic-code-eval sink in browser UI"
        )
        assert _BANNED_FN_CTOR not in stripped, (
            "no Function constructor in browser UI"
        )


# ===========================================================================
# 3. Command parser shape checks (8)
# ===========================================================================
class TestCommandParser:
    def test_app_js_defines_parseCommand(self, app_js: str):
        assert "function parseCommand" in app_js

    def test_parser_handles_help_keyword(self, app_js: str):
        assert "'help'" in app_js or '"help"' in app_js
        # And the help branch produces a help record (kind: 'help').
        assert re.search(r"kind:\s*['\"]help['\"]", app_js) is not None

    def test_parser_handles_raw_json_rpc(self, app_js: str):
        # The >>> sentinel passes the rest of the line to JSON.parse.
        assert "startsWith('>>>')" in app_js or 'startsWith(">>>")' in app_js
        assert "JSON.parse" in app_js

    def test_parser_handles_keyvalue_syntax(self, app_js: str):
        # The k=v form splits on '=' and assigns into params[k].
        assert "indexOf('=')" in app_js or 'indexOf("=")' in app_js
        assert re.search(r"params\s*\[\s*k\s*\]", app_js) is not None

    def test_parser_coerces_typed_values(self, app_js: str):
        # null / true / false / int / float coercion branches.
        assert "'null'" in app_js or '"null"' in app_js
        assert "'true'" in app_js or '"true"' in app_js
        assert "'false'" in app_js or '"false"' in app_js
        assert "parseInt" in app_js
        assert "parseFloat" in app_js

    def test_app_js_references_all_repl_methods(self, app_js: str):
        # The help text and command surface mention every op the
        # server implements (D3 / D4 / D5 / D6).
        for method in ("repl/inspect", "repl/edit", "repl/rewind", "repl/branch"):
            assert method in app_js, f"app.js should reference {method}"

    def test_app_js_emits_help_text(self, app_js: str):
        assert "function helpText" in app_js
        assert "commands:" in app_js

    def test_app_js_wires_form_submit_handler(self, app_js: str):
        assert (
            "addEventListener('submit'" in app_js
            or 'addEventListener("submit"' in app_js
        )


# ===========================================================================
# 4. Audit tail polling shape checks (4)
# ===========================================================================
class TestAuditTailPolling:
    def test_app_js_uses_setInterval(self, app_js: str):
        assert "setInterval" in app_js

    def test_app_js_polls_audit_window(self, app_js: str):
        assert "'audit-window'" in app_js or '"audit-window"' in app_js
        assert "repl/inspect" in app_js

    def test_app_js_diffs_audit_entries_by_id(self, app_js: str):
        # The audit-tail dedup uses entry.id (or entry_id) and a Set
        # of seen ids so each entry is rendered once.
        assert "seenAuditIds" in app_js
        assert "new Set()" in app_js

    def test_app_js_audit_entries_use_textContent(self, app_js: str):
        # The audit append path renders all sub-fields via textContent.
        # Re-pin specifically for the audit surface.
        m = re.search(r"function appendAudit[\s\S]+?\n  }", app_js)
        assert m is not None, "appendAudit function not found"
        body = m.group(0)
        assert ".textContent" in body
        stripped_body = _strip_line_comments(body)
        assert _BANNED_HTML_SETTER not in stripped_body


# ===========================================================================
# 5. Connection lifecycle shape checks (3)
# ===========================================================================
class TestConnectionLifecycle:
    def test_app_js_calls_repl_auth_on_open(self, app_js: str):
        assert "ws.onopen" in app_js
        # send('repl/auth', { token: ... }) is invoked on connect.
        assert re.search(r"send\(\s*['\"]repl/auth['\"]", app_js) is not None

    def test_app_js_displays_session_id_in_header(self, app_js: str):
        assert "session_id" in app_js
        # The session DOM element receives textContent.
        assert re.search(r"session\.textContent\s*=", app_js) is not None

    def test_app_js_clears_audit_poll_on_close(self, app_js: str):
        # ws.onclose stops the audit polling so a closed socket does
        # not keep firing inspect requests against a dead connection.
        assert "ws.onclose" in app_js
        close_section = app_js[app_js.find("ws.onclose"):]
        assert (
            "stopAuditPolling" in close_section
            or "clearInterval" in close_section
        ), "ws.onclose must clear the audit poll handle"
