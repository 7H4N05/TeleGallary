#!/usr/bin/env python3
"""
Quick start script — starts the TeleGallery backend.
Run from the project root: python start.py
"""
import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
BACKEND = ROOT / "backend"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
(DATA / "sessions").mkdir(exist_ok=True)

os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

print("=" * 50)
print("  TeleGallery Backend")
print("  http://127.0.0.1:8000")
print("  API docs: http://127.0.0.1:8000/docs")
print("=" * 50)

subprocess.run([
    sys.executable, "-m", "uvicorn", "main:app",
    "--host", "127.0.0.1",
    "--port", "8000",
    "--reload",
    "--log-level", "info",
], check=True)
