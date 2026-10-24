"""Allow running the CLI with ``python -m kitup``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
