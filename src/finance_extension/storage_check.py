"""Small command-line probe for the mandatory local-path policy."""

from __future__ import annotations

import argparse
from .storage_policy import validate_runtime_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    print(validate_runtime_path(args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
