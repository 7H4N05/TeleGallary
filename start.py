#!/usr/bin/env python3
"""
Quick start script — starts the TeleGallery backend.
Run from the project root: python start.py

Uses backend/venv when present (recommended). Plain `python start.py` without
a venv often picks the wrong interpreter on Windows (e.g. MSYS Python 3.14).
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
BACKEND = ROOT / "backend"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
(DATA / "sessions").mkdir(exist_ok=True)


def _venv_python() -> Path | None:
    if sys.platform == "win32":
        candidate = BACKEND / "venv" / "Scripts" / "python.exe"
    else:
        candidate = BACKEND / "venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def _python_for_server() -> str:
    venv_py = _venv_python()
    if venv_py is not None:
        return str(venv_py)
    return sys.executable


def _has_uvicorn(python: str) -> bool:
    proc = subprocess.run(
        [python, "-c", "import uvicorn"],
        capture_output=True,
    )
    return proc.returncode == 0


def main() -> None:
    python = _python_for_server()

    if not _has_uvicorn(python):
        print("=" * 50)
        print("  uvicorn is not installed for this Python:")
        print(f"  {python}")
        print()
        if _venv_python() is None:
            print("  Create the project venv and install deps:")
            print("    .\\scripts\\start-backend.ps1")
            print("  Or manually:")
            print("    py -m venv backend\\venv")
            print("    backend\\venv\\Scripts\\pip install -r backend\\requirements.txt")
        else:
            print("  Install dependencies into the project venv:")
            print("    backend\\venv\\Scripts\\pip install -r backend\\requirements.txt")
            print("  Or run:")
            print("    .\\scripts\\start-backend.ps1")
        print("=" * 50)
        sys.exit(1)

    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))

    print("=" * 50)
    print("  TeleGallery Backend")
    print("  http://127.0.0.1:8000")
    print("  API docs: http://127.0.0.1:8000/docs")
    if python != sys.executable:
        print(f"  Python: {python}")
    print("=" * 50)

    subprocess.run(
        [
            python,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--reload",
            "--log-level",
            "info",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
