"""CAM — Consent Asset Matcher API"""

import re
from pathlib import Path
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

from auth import (
    AUTH_COOKIE_NAME,
    create_session_token,
    is_configured,
    is_password_valid,
    load_auth_config,
    verify_session_token,
)
import canto_client as canto
from pdf_parser import parse_consent_pdf
from matcher import score_match

load_dotenv()

app = FastAPI(title="CAM — Consent Asset Matcher")

PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/static/colors_and_type.css",
    "/static/fairpicture.svg",
    "/static/fonts/GT-Cinetype-Bold.woff",
    "/static/fonts/GT-Cinetype-Bold.woff2",
    "/static/fonts/GT-Cinetype-Mono.woff",
    "/static/fonts/GT-Cinetype-Mono.woff2",
    "/static/fonts/GT-Cinetype-Regular.woff",
    "/static/fonts/GT-Cinetype-Regular.woff2",
}
PUBLIC_PREFIXES = ()


def _wants_json(request: Request) -> bool:
    return request.url.path == "/api" or request.url.path.startswith("/api/")


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _login_html(error: str | None = None, status_code: int = 200) -> HTMLResponse:
    error_html = f'<p class="error">{error}</p>' if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CAM Login</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, system-ui, -apple-system, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f6f4ef; color: #17201a; }}
    main {{ width: min(100% - 32px, 380px); padding: 32px; border: 1px solid #d8d2c5; border-radius: 8px; background: #fff; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    p {{ margin: 0 0 24px; color: #4e5b52; }}
    label {{ display: block; margin-bottom: 8px; font-weight: 700; }}
    input {{ box-sizing: border-box; width: 100%; height: 44px; border: 1px solid #b9b2a6; border-radius: 6px; padding: 0 12px; font: inherit; }}
    button {{ width: 100%; height: 44px; margin-top: 16px; border: 0; border-radius: 6px; background: #17201a; color: #fff; font: inherit; font-weight: 700; cursor: pointer; }}
    .error {{ margin: 0 0 16px; color: #a1261f; font-weight: 700; }}
  </style>
</head>
<body>
  <main>
    <h1>CAM</h1>
    <p>Consent Asset Matcher</p>
    {error_html}
    <form method="post" action="/login">
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>""",
        status_code=status_code,
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_public_path(request.url.path):
            return await call_next(request)

        config = load_auth_config()
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if verify_session_token(token, config):
            return await call_next(request)

        if _wants_json(request):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        return RedirectResponse("/login", status_code=303)


app.add_middleware(AuthMiddleware)

# In-memory decisions store: {image_id: "confirmed" | "rejected"}
decisions: dict[str, dict] = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_pdf_data(doc: dict) -> dict | None:
    url = doc.get("url", {}).get("directUrlOriginal", "")
    if not url:
        return None
    try:
        raw = canto.download_pdf_cached(doc["id"], url)
        data = parse_consent_pdf(raw)
        data["_id"] = doc["id"]
        data["_name"] = doc.get("name", "")
        data["_url"] = doc.get("url", {}).get("detail", "")
        return data
    except Exception:
        return None


def _project_id_from_albums(asset: dict) -> str | None:
    for a in asset.get("relatedAlbums", []):
        m = re.search(r"[_/](\d{3,5})[_/]", a.get("namePath", ""))
        if m:
            return m.group(1)
    return None


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return _login_html()


@app.post("/login", response_class=HTMLResponse)
def login(password: str = Form(...)):
    config = load_auth_config()
    if not is_configured(config):
        return _login_html("Authentication is not configured.", status_code=500)

    if not is_password_valid(password, config):
        return _login_html("Invalid password.", status_code=401)

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        create_session_token(config),
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


@app.get("/api/projects")
def list_projects():
    """Return all project folders from Canto."""
    tree = canto.get_folder_tree()
    projects = []
    for folder in tree:
        m = re.search(r"[_](\d{3,5})[_]", folder.get("name", ""))
        pid = m.group(1) if m else None
        projects.append({
            "id": folder["id"],
            "name": folder["name"],
            "project_id": pid,
            "albums": [{"id": c["id"], "name": c["name"]} for c in folder.get("children", [])],
        })
    return projects


@app.get("/api/matches/{project_id}")
def get_matches(project_id: str):
    """Run matching for a project and return scored pairs."""
    images = canto.get_project_images(project_id)
    docs   = canto.get_project_documents(project_id)

    if not images:
        raise HTTPException(404, f"No images found for project {project_id}")
    if not docs:
        raise HTTPException(404, f"No documents found for project {project_id}")

    # Parse PDFs
    pdf_data_list = [d for d in (_load_pdf_data(doc) for doc in docs
                                  if doc.get("name", "").lower().endswith(".pdf"))
                     if d is not None]

    results = []
    for image in images:
        best_result, best_pdf = None, None
        all_candidates = []

        for pdf_data in pdf_data_list:
            result = score_match(image, pdf_data)
            all_candidates.append({
                "pdf_id":    pdf_data["_id"],
                "pdf_name":  pdf_data["_name"],
                "pdf_url":   pdf_data["_url"],
                "pdf_names": pdf_data.get("all_names", []),
                "score":     round(result.overall, 1),
                "tier":      result.tier,
                "signals": {s.signal: {"score": round(s.score, 1), "detail": s.detail}
                            for s in result.signals},
            })
            if best_result is None or result.overall > best_result.overall:
                best_result, best_pdf = result, pdf_data

        all_candidates.sort(key=lambda c: c["score"], reverse=True)

        decision = decisions.get(image["id"], {})
        img_additional = image.get("additional", {})

        results.append({
            "image_id":      image["id"],
            "image_name":    image.get("name", ""),
            "image_thumb":   image.get("url", {}).get("previewURI240", ""),
            "person_shown":  img_additional.get("Person Shown in the Image") or "",
            "country":       img_additional.get("Country") or "",
            "city":          img_additional.get("City") or "",
            "consent_linked": img_additional.get("Consent") or "",
            "project_id":    project_id,
            "best_match": {
                "pdf_id":   best_pdf["_id"]   if best_pdf else None,
                "pdf_name": best_pdf["_name"] if best_pdf else None,
                "pdf_url":  best_pdf["_url"]  if best_pdf else None,
                "score":    round(best_result.overall, 1) if best_result else 0,
                "tier":     best_result.tier if best_result else "skip",
                "signals":  {s.signal: {"score": round(s.score, 1), "detail": s.detail}
                             for s in best_result.signals} if best_result else {},
            } if best_pdf else None,
            "candidates":    all_candidates[:5],  # top 5
            "decision":      decision.get("action", ""),
            "decided_pdf_id": decision.get("pdf_id", ""),
        })

    # Summary stats — detailed breakdown
    total            = len(results)
    confirmed        = sum(1 for r in results if r["decision"] == "confirmed")
    rejected         = sum(1 for r in results if r["decision"] == "rejected")
    auto_ready       = sum(1 for r in results if r.get("best_match") and
                           r["best_match"]["tier"] == "auto" and not r["decision"])
    needs_review     = sum(1 for r in results if r.get("best_match") and
                           r["best_match"]["tier"] == "review" and not r["decision"])
    no_person_meta   = sum(1 for r in results if not r["person_shown"])
    no_consent_found = sum(1 for r in results if not r.get("best_match") or
                           r["best_match"]["score"] == 0)
    # Images where person is known but no matching consent PDF was found
    unresolvable     = sum(1 for r in results if r["person_shown"] and
                           (not r.get("best_match") or r["best_match"]["score"] == 0))

    return {
        "project_id": project_id,
        "stats": {
            "total":            total,
            "confirmed":        confirmed,
            "rejected":         rejected,
            "auto_ready":       auto_ready,
            "needs_review":     needs_review,
            "no_person_meta":   no_person_meta,
            "no_consent_found": no_consent_found,
            "unresolvable":     unresolvable,
            "compliance":       round(confirmed / total * 100, 1) if total else 0,
        },
        "matches": results,
    }


class DecisionRequest(BaseModel):
    image_id:  str
    pdf_id:    str
    action:    str   # "confirmed" | "rejected"


@app.post("/api/decision")
def set_decision(req: DecisionRequest):
    """Record a confirm or reject decision. On confirm, links assets in Canto."""
    if req.action not in ("confirmed", "rejected"):
        raise HTTPException(400, "action must be 'confirmed' or 'rejected'")

    decisions[req.image_id] = {"action": req.action, "pdf_id": req.pdf_id}

    if req.action == "confirmed":
        success = canto.update_consent_field(req.image_id, req.pdf_id)
        return {"status": "ok", "linked_in_canto": success}

    return {"status": "ok", "linked_in_canto": False}


@app.post("/api/decision/bulk-confirm")
def bulk_confirm(project_id: str):
    """Auto-confirm all AUTO-tier unreviewed matches for a project."""
    data = get_matches(project_id)
    confirmed = 0
    for m in data["matches"]:
        if (m.get("best_match") and
                m["best_match"]["tier"] == "auto" and
                not m["decision"]):
            req = DecisionRequest(
                image_id=m["image_id"],
                pdf_id=m["best_match"]["pdf_id"],
                action="confirmed",
            )
            set_decision(req)
            confirmed += 1
    return {"confirmed": confirmed}


# ── static files (frontend) ───────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

@app.get("/")
def root():
    return FileResponse(BASE_DIR / "static" / "index.html")
