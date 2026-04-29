"""Entry point for ``python -m persistence.repl``.

Delegates to :func:`persistence.repl._cli.main`. The CLI surface is
thin — see ``_cli.py`` for the ``mint`` / ``list`` / ``revoke``
subcommands.
"""
from __future__ import annotations

import sys

from ._cli import main

if __name__ == "__main__":
    sys.exit(main())
