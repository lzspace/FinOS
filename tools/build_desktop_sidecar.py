"""Build the local Python Application API as a Tauri sidecar."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    host = subprocess.run(
        ["rustc", "-vV"],
        check=True,
        capture_output=True,
        text=True,
    )
    target = next(
        line.split(":", 1)[1].strip()
        for line in host.stdout.splitlines()
        if line.startswith("host:")
    )
    output = ROOT / "build" / "desktop-worker"
    environment = {
        **os.environ,
        "PYINSTALLER_CONFIG_DIR": str(output / "config"),
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "finance-desktop-worker",
            "--distpath",
            str(output),
            "--workpath",
            str(output / "work"),
            "--specpath",
            str(output),
            "--paths",
            str(ROOT / "src"),
            "--hidden-import",
            "keyring.backends.macOS",
            "--exclude-module",
            "keyring.devpi_client",
            "--exclude-module",
            "keyring.http",
            "--exclude-module",
            "keyring.testing",
            "--exclude-module",
            "requests",
            "--exclude-module",
            "urllib3",
            "--exclude-module",
            "certifi",
            "--exclude-module",
            "charset_normalizer",
            "--collect-all",
            "rfc3987_syntax",
            "--add-data",
            f"{ROOT / 'extensions' / 'finance'}:extensions/finance",
            "--add-data",
            f"{ROOT / 'src' / 'finance_extension' / 'release_integrity.json'}:finance_extension",
            "--add-data",
            f"{ROOT / 'ui' / 'dist'}:finance_extension/ui",
            str(ROOT / "tools" / "desktop_worker_entry.py"),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    destination = (
        ROOT
        / "src-tauri"
        / "binaries"
        / f"finance-desktop-worker-{target}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output / "finance-desktop-worker", destination)
    destination.chmod(0o755)
    print(f"Desktop worker sidecar: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
