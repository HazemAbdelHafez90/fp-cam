import sys
from pathlib import Path

# Make the project root importable from this subdirectory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cam_app import app  # noqa: F401 — Vercel looks for `app`
