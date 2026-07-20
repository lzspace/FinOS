"""Module entry point for the repository safety guard."""

from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    source = Path(__file__).resolve().parents[2] / "extensions/finance/tools/finance_repo_guard.py"
    installed = (
        Path(__file__).resolve().parents[1] / "extensions/finance/tools/finance_repo_guard.py"
    )
    runpy.run_path(
        str(source if source.exists() else installed),
        run_name="__main__",
    )
