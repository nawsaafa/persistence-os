"""PII redact handler — pluggable schema-based redaction before outbound ops."""
from persistence.effect.handlers.pii_redact import (
    REDACTED,
    make_pii_redact_handler,
)
from persistence.effect.runtime import Handler, Runtime, perform, with_runtime


HOTEL_GUEST_SCHEMA = {
    # top-level arg names whose values must be redacted
    "fields": {"guest_name", "phone", "email"},
    # dotted-path deep fields (for nested dicts)
    "paths": {"meta.email"},
}


def _capture_net():
    captured: list = []

    def clause(args, k, ctx):
        captured.append(args)
        return {"status": 200, "body": b"ok"}

    h = Handler(name="raw", wraps={"net/fetch"}, clauses={"net/fetch": clause})
    return h, captured


def test_redacts_top_level_pii_field():
    raw, captured = _capture_net()
    pii = make_pii_redact_handler(schema=HOTEL_GUEST_SCHEMA, wraps={"net/fetch"})
    rt = Runtime([raw, pii])
    with with_runtime(rt):
        perform(
            "net/fetch",
            url="https://hotel.api/x",
            method="POST",
            body={"guest_name": "Alice", "room": 12},
        )
    # body must have guest_name redacted.
    sent = captured[0]
    assert sent["body"]["guest_name"] == REDACTED
    assert sent["body"]["room"] == 12


def test_redacts_nested_path():
    raw, captured = _capture_net()
    pii = make_pii_redact_handler(schema=HOTEL_GUEST_SCHEMA, wraps={"net/fetch"})
    rt = Runtime([raw, pii])
    with with_runtime(rt):
        perform(
            "net/fetch",
            url="https://hotel.api/x",
            method="POST",
            body={"meta": {"email": "alice@example.com", "role": "guest"}},
        )
    sent = captured[0]
    assert sent["body"]["meta"]["email"] == REDACTED
    assert sent["body"]["meta"]["role"] == "guest"


def test_redacts_headers_when_schema_says_so():
    schema = {"fields": {"authorization"}, "paths": set()}
    raw, captured = _capture_net()
    pii = make_pii_redact_handler(schema=schema, wraps={"net/fetch"})
    rt = Runtime([raw, pii])
    with with_runtime(rt):
        perform(
            "net/fetch",
            url="x",
            headers={"authorization": "Bearer supersecret", "x-tenant": "t1"},
        )
    sent = captured[0]
    assert sent["headers"]["authorization"] == REDACTED
    assert sent["headers"]["x-tenant"] == "t1"


def test_original_args_dict_is_not_mutated():
    raw, captured = _capture_net()
    pii = make_pii_redact_handler(schema=HOTEL_GUEST_SCHEMA, wraps={"net/fetch"})
    rt = Runtime([raw, pii])
    original_body = {"guest_name": "Alice"}
    with with_runtime(rt):
        perform("net/fetch", url="x", body=original_body)
    # The caller's body must not be mutated in place.
    assert original_body == {"guest_name": "Alice"}


def test_schema_without_any_matches_passes_through():
    raw, captured = _capture_net()
    pii = make_pii_redact_handler(
        schema={"fields": {"not-present"}, "paths": set()}, wraps={"net/fetch"}
    )
    rt = Runtime([raw, pii])
    with with_runtime(rt):
        perform("net/fetch", url="x", body={"room": 12})
    assert captured[0]["body"] == {"room": 12}
