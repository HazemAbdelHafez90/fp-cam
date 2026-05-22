import sys
from pathlib import Path

# Vercel installs packages via uv into a venv at build time.
# The venv is bundled into the Lambda at /var/task but not activated,
# so we add its site-packages to sys.path manually.
_task = Path("/var/task")
for _py in ["python3.12", "python3.11", "python3.10", "python3.9"]:
    _sp = _task / ".vercel" / "python" / ".venv" / "lib" / _py / "site-packages"
    if _sp.exists():
        sys.path.insert(0, str(_sp))
        break

# Make the project root importable
sys.path.insert(0, str(_task))

from cam_app import app  # noqa: F401 — Vercel looks for `app`
