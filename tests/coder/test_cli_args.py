"""Phase 2.1b — CLI arg parsing tests (--provider, --model)."""
from __future__ import annotations

import pytest


def test_provider_flag_default_is_auto():
    from persistence.coder._cli import build_parser

    args = build_parser().parse_args(["--task", "t"])
    assert args.provider == "auto"


def test_provider_flag_accepts_anthropic():
    from persistence.coder._cli import build_parser

    args = build_parser().parse_args(["--task", "t", "--provider", "anthropic"])
    assert args.provider == "anthropic"


def test_provider_flag_accepts_claude_code():
    from persistence.coder._cli import build_parser

    args = build_parser().parse_args(["--task", "t", "--provider", "claude-code"])
    assert args.provider == "claude-code"


def test_provider_flag_rejects_unknown():
    from persistence.coder._cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--task", "t", "--provider", "ollama"])


def test_model_flag_default_is_claude_opus_4_7():
    from persistence.coder._cli import build_parser

    args = build_parser().parse_args(["--task", "t"])
    assert args.model == "claude-opus-4-7"


def test_model_flag_can_be_overridden():
    from persistence.coder._cli import build_parser

    args = build_parser().parse_args(["--task", "t", "--model", "claude-haiku-4-5-20251001"])
    assert args.model == "claude-haiku-4-5-20251001"
