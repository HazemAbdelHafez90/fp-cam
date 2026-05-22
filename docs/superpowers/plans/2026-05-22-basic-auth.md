# Basic Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add free, basic shared-password authentication to the CAM FastAPI app and protect both the UI and API routes.

**Architecture:** Add a small `auth.py` module for password checks, signed cookie creation, and signed cookie verification. Wire it into `cam_app.py` with middleware plus `/login` and `/logout` routes; static assets and login routes stay public, all app/API/docs routes become protected.

**Tech Stack:** Python 3, FastAPI, Starlette middleware/responses, `itsdangerous` for signed cookies, `python-multipart` for form parsing, `pytest` and FastAPI `TestClient` for focused auth tests.

---

## File Structure

- Create `auth.py`: owns auth configuration, password validation, signed session tokens, cookie naming, and cookie settings.
- Create `tests/test_auth.py`: unit tests for password and token behavior.
- Create `tests/test_cam_auth.py`: FastAPI boundary tests for login, logout, redirects, and protected API behavior.
- Modify `cam_app.py`: add auth middleware, login/logout routes, login HTML response, and protected route behavior.
- Modify `requirements.txt`: add `itsdangerous`, `python-multipart`, `pytest`, and `httpx`.
- Modify `.env.example`: document `CAM_AUTH_PASSWORD` and `CAM_SESSION_SECRET`.

## Task 1: Add Auth Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update dependency list**

Edit `requirements.txt` so it contains:

```txt
fastapi
requests
pdfplumber
rapidfuzz
python-dotenv
rich
itsdangerous
python-multipart
pytest
httpx
```

- [ ] **Step 2: Verify dependency file**

Run:

```bash
sed -n '1,40p' requirements.txt
```

Expected: the output includes `itsdangerous`, `python-multipart`, `pytest`, and `httpx`.

- [ ] **Step 3: Commit dependency update**

```bash
git add requirements.txt
git commit -m "Add auth test dependencies"
```

## Task 2: Auth Helper Tests

**Files:**
- Create: `tests/test_auth.py`
- Create later: `auth.py`

- [ ] **Step 1: Write failing auth helper tests**

Create `tests/test_auth.py`:

```python
import time

import pytest

from auth import (
    AUTH_COOKIE_NAME,
    AuthConfig,
    create_session_token,
    is_password_valid,
    verify_session_token,
)


def test_password_validation_accepts_configured_password():
    config = AuthConfig(password="correct-password", session_secret="secret")

    assert is_password_valid("correct-password", config) is True


def test_password_validation_rejects_wrong_password():
    config = AuthConfig(password="correct-password", session_secret="secret")

    assert is_password_valid("wrong-password", config) is False


def test_password_validation_rejects_missing_config():
    config = AuthConfig(password="", session_secret="secret")

    assert is_password_valid("anything", config) is False


def test_session_token_round_trip():
    config = AuthConfig(password="correct-password", session_secret="secret")

    token = create_session_token(config)

    assert verify_session_token(token, config) is True


def test_invalid_session_token_is_rejected():
    config = AuthConfig(password="correct-password", session_secret="secret")

    assert verify_session_token("not-a-valid-token", config) is False


def test_session_token_signed_with_old_secret_is_rejected():
    original = AuthConfig(password="correct-password", session_secret="old-secret")
    rotated = AuthConfig(password="correct-password", session_secret="new-secret")

    token = create_session_token(original)

    assert verify_session_token(token, rotated) is False


def test_session_token_expires():
    config = AuthConfig(password="correct-password", session_secret="secret", max_age_seconds=1)
    token = create_session_token(config)

    time.sleep(1.1)

    assert verify_session_token(token, config) is False


def test_cookie_name_is_stable():
    assert AUTH_COOKIE_NAME == "cam_session"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_auth.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'auth'`.

## Task 3: Implement Auth Helper

**Files:**
- Create: `auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Create `auth.py`**

Create `auth.py`:

```python
"""Basic shared-password authentication helpers for CAM."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


AUTH_COOKIE_NAME = "cam_session"
AUTH_COOKIE_VALUE = "authenticated"
DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


@dataclass(frozen=True)
class AuthConfig:
    password: str
    session_secret: str
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
    cookie_secure: bool = False


def load_auth_config() -> AuthConfig:
    cookie_secure = os.getenv("VERCEL") == "1" or os.getenv("CAM_AUTH_COOKIE_SECURE", "").lower() == "true"
    return AuthConfig(
        password=os.getenv("CAM_AUTH_PASSWORD", ""),
        session_secret=os.getenv("CAM_SESSION_SECRET", ""),
        cookie_secure=cookie_secure,
    )


def is_configured(config: AuthConfig) -> bool:
    return bool(config.password and config.session_secret)


def is_password_valid(candidate: str, config: AuthConfig) -> bool:
    if not is_configured(config):
        return False
    return hmac.compare_digest(candidate, config.password)


def _serializer(config: AuthConfig) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt="cam-auth-session")


def create_session_token(config: AuthConfig) -> str:
    if not config.session_secret:
        raise RuntimeError("CAM_SESSION_SECRET must be configured")
    return _serializer(config).dumps(AUTH_COOKIE_VALUE)


def verify_session_token(token: str | None, config: AuthConfig) -> bool:
    if not token or not is_configured(config):
        return False
    try:
        value = _serializer(config).loads(token, max_age=config.max_age_seconds)
    except (BadSignature, SignatureExpired):
        return False
    return value == AUTH_COOKIE_VALUE
```

- [ ] **Step 2: Run auth helper tests**

Run:

```bash
pytest tests/test_auth.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit auth helper**

```bash
git add auth.py tests/test_auth.py
git commit -m "Add shared password auth helpers"
```

## Task 4: FastAPI Auth Boundary Tests

**Files:**
- Create: `tests/test_cam_auth.py`
- Modify later: `cam_app.py`

- [ ] **Step 1: Write failing FastAPI auth tests**

Create `tests/test_cam_auth.py`:

```python
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CAM_AUTH_PASSWORD", "let-me-in")
    monkeypatch.setenv("CAM_SESSION_SECRET", "test-session-secret")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("CAM_AUTH_COOKIE_SECURE", raising=False)

    import cam_app

    importlib.reload(cam_app)
    return TestClient(cam_app.app)


def test_root_redirects_to_login_without_session(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_api_returns_401_without_session(client):
    response = client.get("/api/projects")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_login_page_is_public(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert "CAM" in response.text
    assert "password" in response.text.lower()


def test_wrong_password_is_rejected(client):
    response = client.post("/login", data={"password": "wrong-password"}, follow_redirects=False)

    assert response.status_code == 401
    assert "Invalid password" in response.text
    assert "cam_session" not in response.cookies


def test_correct_password_sets_cookie_and_redirects(client):
    response = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "cam_session" in response.cookies
    assert response.cookies["cam_session"]


def test_authenticated_root_loads_index(client):
    login = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    response = client.get("/", cookies=login.cookies)

    assert response.status_code == 200
    assert "Consent Asset Matcher" in response.text


def test_authenticated_api_reaches_route(client, monkeypatch):
    import cam_app

    monkeypatch.setattr(cam_app.canto, "get_folder_tree", lambda: [])
    login = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    response = client.get("/api/projects", cookies=login.cookies)

    assert response.status_code == 200
    assert response.json() == []


def test_logout_clears_cookie(client):
    login = client.post("/login", data={"password": "let-me-in"}, follow_redirects=False)

    response = client.get("/logout", cookies=login.cookies, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.cookies.get("cam_session") in ("", None)
```

- [ ] **Step 2: Run FastAPI auth tests to verify they fail**

Run:

```bash
pytest tests/test_cam_auth.py -v
```

Expected: FAIL because `/login` and auth middleware do not exist yet.

## Task 5: Wire Auth Into FastAPI

**Files:**
- Modify: `cam_app.py`
- Test: `tests/test_cam_auth.py`

- [ ] **Step 1: Add imports to `cam_app.py`**

Add these imports near the existing FastAPI imports:

```python
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
```

Replace the existing narrower imports:

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
```

Add auth imports after local imports:

```python
from auth import (
    AUTH_COOKIE_NAME,
    create_session_token,
    is_configured,
    is_password_valid,
    load_auth_config,
    verify_session_token,
)
```

- [ ] **Step 2: Add auth constants and helpers in `cam_app.py`**

Insert after `decisions: dict[str, dict] = {}`:

```python
PUBLIC_PATHS = {"/login"}
PUBLIC_PREFIXES = ("/static/",)


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _login_html(error: str = "") -> str:
    error_html = f'<div class="login-error">{error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Login - CAM</title>
  <link rel="stylesheet" href="/static/colors_and_type.css" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: var(--fp-secondary-bg);
      color: var(--fp-darkgrey);
      font-family: var(--font-sans);
    }}
    .login-panel {{
      width: min(420px, calc(100vw - 32px));
      background: var(--fp-white);
      border: 1px solid var(--fp-secondary-25);
      border-radius: var(--radius-sm);
      padding: 28px;
      box-shadow: var(--shadow-card);
    }}
    .login-logo {{ height: 22px; margin-bottom: 22px; }}
    h1 {{ font-size: var(--fs-xl); line-height: var(--lh-xl); margin-bottom: 8px; }}
    p {{ font-size: var(--fs-sm); color: var(--fp-secondary-100); margin-bottom: 20px; }}
    label {{ display: block; font-size: var(--fs-xs); font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--fp-secondary-100); margin-bottom: 6px; }}
    input {{
      width: 100%;
      font: inherit;
      border: 1px solid var(--fp-secondary-100);
      border-radius: var(--radius-sm);
      padding: 10px 12px;
      margin-bottom: 14px;
    }}
    input:focus {{ border-color: var(--fp-primary); box-shadow: var(--shadow-input); outline: none; }}
    button {{
      width: 100%;
      font: inherit;
      font-weight: 700;
      border: 1px solid var(--fp-primary);
      border-radius: var(--radius-sm);
      background: var(--fp-primary);
      color: var(--fp-white);
      padding: 10px 14px;
      cursor: pointer;
    }}
    button:hover {{ background: var(--fp-primary-hover); border-color: var(--fp-primary-hover); }}
    .login-error {{
      background: var(--fp-light-primary);
      border: 1px solid var(--fp-primary);
      border-radius: var(--radius-sm);
      color: var(--fp-darkgrey);
      padding: 9px 10px;
      font-size: var(--fs-sm);
      margin-bottom: 14px;
    }}
  </style>
</head>
<body>
  <main class="login-panel">
    <img src="/static/fairpicture.svg" alt="Fairpicture" class="login-logo" />
    <h1>CAM Login</h1>
    <p>Enter the shared password to continue.</p>
    {error_html}
    <form method="post" action="/login">
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" autofocus required />
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>"""
```

- [ ] **Step 3: Add middleware class in `cam_app.py`**

Insert after the helper block:

```python
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        config = load_auth_config()
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if verify_session_token(token, config):
            return await call_next(request)

        if _wants_json(request):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        return RedirectResponse("/login", status_code=303)
```

Register it immediately after `app = FastAPI(...)`:

```python
app.add_middleware(AuthMiddleware)
```

- [ ] **Step 4: Add login and logout routes in `cam_app.py`**

Insert before the existing `/api/projects` route:

```python
@app.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse(_login_html())


@app.post("/login")
def login(password: str = Form("")):
    config = load_auth_config()
    if not is_configured(config):
        return HTMLResponse(_login_html("Authentication is not configured."), status_code=500)

    if not is_password_valid(password, config):
        return HTMLResponse(_login_html("Invalid password."), status_code=401)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=create_session_token(config),
        max_age=config.max_age_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response
```

- [ ] **Step 5: Run FastAPI auth tests**

Run:

```bash
pytest tests/test_cam_auth.py -v
```

Expected: PASS.

- [ ] **Step 6: Run auth helper tests**

Run:

```bash
pytest tests/test_auth.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit FastAPI auth wiring**

```bash
git add cam_app.py tests/test_cam_auth.py
git commit -m "Protect CAM with shared password auth"
```

## Task 6: Environment Documentation

**Files:**
- Modify: `.env.example`
- Test: manual file inspection

- [ ] **Step 1: Update `.env.example`**

Edit `.env.example` so it contains:

```env
CANTO_BASE_URL=https://yourcompany.canto.global
CANTO_APP_ID=your_app_id
CANTO_APP_SECRET=your_app_secret

CAM_AUTH_PASSWORD=change-me
CAM_SESSION_SECRET=generate-a-long-random-string
```

- [ ] **Step 2: Verify `.env.example`**

Run:

```bash
sed -n '1,80p' .env.example
```

Expected: output includes `CAM_AUTH_PASSWORD` and `CAM_SESSION_SECRET`, and does not include any real secret values.

- [ ] **Step 3: Commit env documentation**

```bash
git add .env.example
git commit -m "Document CAM auth environment variables"
```

## Task 7: Full Verification

**Files:**
- Verify all touched files

- [ ] **Step 1: Run all tests**

Run:

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 2: Run compile/import checks**

Run:

```bash
python3 -m py_compile main.py cam_app.py canto_client.py matcher.py pdf_parser.py api/index.py auth.py
```

Expected: no output and exit code 0.

Run:

```bash
python3 -c "import api.index; print(api.index.app.title)"
```

Expected:

```txt
CAM — Consent Asset Matcher
```

- [ ] **Step 3: Confirm git state**

Run:

```bash
git status --short --branch
```

Expected: branch is ahead of `origin/main` with no unstaged or staged changes after the task commits.

## Task 8: Deployment Handoff

**Files:**
- No code changes unless verification reveals an issue

- [ ] **Step 1: Tell the operator which Vercel env vars are required**

Before pushing, confirm the deployment needs:

```txt
CAM_AUTH_PASSWORD=<shared password>
CAM_SESSION_SECRET=<long random string>
```

Suggested secret generation command:

```bash
openssl rand -base64 32
```

- [ ] **Step 2: Push when requested**

Run only after env vars are ready or the operator accepts that the deployed app will fail closed until they are set:

```bash
git push origin main
```

Expected: push succeeds and Vercel deployment starts automatically.

## Self-Review

Spec coverage:

- Shared password: Task 2 and Task 3.
- Signed HTTP-only cookie: Task 3 and Task 5.
- Protected UI/API/docs routes: Task 4 and Task 5.
- Public login/static assets: Task 4 and Task 5.
- Login/logout flow: Task 4 and Task 5.
- Missing env behavior: Task 5.
- Deployment env docs: Task 6 and Task 8.
- Verification: Task 7.

Red flag scan: no placeholder tasks remain.

Type consistency: `AuthConfig`, `AUTH_COOKIE_NAME`, `create_session_token`, `verify_session_token`, `is_configured`, and `is_password_valid` are defined in Task 3 and consumed consistently in Tasks 2 and 5.
