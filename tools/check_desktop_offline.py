"""Fail when the desktop implementation exposes a network transport."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = [
    ROOT / "ui" / "src",
    ROOT / "src-tauri" / "src",
    ROOT / "src" / "finance_extension" / "desktop_worker.py",
]
BLOCKED = re.compile(
    r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|TcpStream|UdpSocket|"
    r"reqwest|hyper::|tauri_plugin_http)\b"
)


def main() -> int:
    violations: list[str] = []
    for root in SOURCE_ROOTS:
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if path.is_file() and path.suffix in {".py", ".rs", ".ts", ".tsx"}:
                if BLOCKED.search(path.read_text(encoding="utf-8")):
                    violations.append(str(path.relative_to(ROOT)))
    config = (ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    if "connect-src 'none'" not in config:
        violations.append("src-tauri/tauri.conf.json: CSP connect-src is not none")
    cargo = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    for forbidden in ("tauri-plugin-http", "tauri-plugin-websocket", "reqwest"):
        if forbidden in cargo:
            violations.append(f"src-tauri/Cargo.toml: {forbidden}")
    if violations:
        raise SystemExit("Desktop offline scan failed:\n" + "\n".join(violations))
    print("Desktop offline scan passed: no network transport is exposed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
