"""PyInstaller entry point for the private Finance desktop worker."""

from finance_extension.desktop_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
