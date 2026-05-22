"""CAM — Consent Asset Matcher API"""

import re
from pathlib import Path
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
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


@app.exception_handler(Exception)
async def _all_exceptions(request: Request, exc: Exception):
    """Always return JSON for /api/* errors so the frontend can parse them."""
    if request.url.path.startswith("/api/"):
        status = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", None) or str(exc)
        return JSONResponse({"detail": detail}, status_code=status)
    raise exc

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
    error_block = (
        f'<div class="error-bar" role="alert">'
        f'<span class="material-icons-round" aria-hidden="true">error_outline</span>'
        f'{error}'
        f'</div>'
    ) if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sign in — CAM</title>
  <link rel="stylesheet" href="/static/colors_and_type.css" />
  <link rel="stylesheet" href="https://fonts.googleapis.com/icon?family=Material+Icons+Round" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: var(--font-sans);
      background: var(--fp-secondary-bg);
      color: var(--fp-darkgrey);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}

    /* ── Header ── */
    header {{
      background: var(--fp-white);
      border-bottom: 1px solid var(--fp-primary);
      height: 56px;
      display: flex;
      align-items: center;
      padding: 0 32px;
      flex-shrink: 0;
    }}
    .logo {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .logo img {{ height: 20px; display: block; }}
    .logo-divider {{
      width: 1px;
      height: 18px;
      background: var(--fp-secondary-25);
    }}
    .logo-product {{
      font-size: var(--fs-sm);
      font-weight: 700;
      color: var(--fp-darkgrey);
    }}
    .logo-product span {{ color: var(--fp-primary); }}

    /* ── Centre stage ── */
    .stage {{
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 48px 24px;
    }}

    /* ── Card ── */
    .card {{
      background: var(--fp-white);
      border: 1px solid var(--fp-secondary-25);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow-pop);
      width: min(100%, 400px);
      overflow: hidden;
    }}
    .card-header {{
      padding: 28px 32px 24px;
      border-bottom: 1px solid var(--fp-primary);
    }}
    .card-title {{
      font-size: var(--fs-xl);
      font-weight: 700;
      line-height: 1.2;
      color: var(--fp-darkgrey);
    }}
    .card-sub {{
      font-size: var(--fs-sm);
      color: var(--fp-secondary-75);
      margin-top: 4px;
    }}
    .card-body {{
      padding: 28px 32px 32px;
    }}

    /* ── Error bar ── */
    .error-bar {{
      display: flex;
      align-items: center;
      gap: 8px;
      background: var(--fp-light-primary);
      border: 1px solid var(--fp-primary);
      border-radius: var(--radius-sm);
      padding: 10px 14px;
      font-size: var(--fs-sm);
      font-weight: 700;
      color: var(--fp-primary);
      margin-bottom: 20px;
    }}
    .error-bar .material-icons-round {{ font-size: 16px; flex-shrink: 0; }}

    /* ── Form ── */
    .field {{ margin-bottom: 20px; }}
    .field:last-of-type {{ margin-bottom: 0; }}

    label {{
      display: block;
      font-size: var(--fs-xs);
      font-weight: 700;
      color: var(--fp-secondary-100);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
    }}
    input[type="password"] {{
      font-family: var(--font-sans);
      font-size: var(--fs-sm);
      width: 100%;
      height: 40px;
      border: 1px solid var(--fp-secondary-100);
      border-radius: var(--radius-sm);
      padding: 0 12px;
      background: var(--fp-white);
      color: var(--fp-darkgrey);
      outline: none;
      transition: border-color .15s, box-shadow .15s;
    }}
    input[type="password"]:focus {{
      border-color: var(--fp-primary);
      box-shadow: var(--shadow-input);
    }}

    .btn-submit {{
      font-family: var(--font-sans);
      font-size: var(--fs-sm);
      font-weight: 700;
      width: 100%;
      height: 40px;
      margin-top: 24px;
      border: 1px solid var(--fp-primary);
      border-radius: var(--radius-sm);
      background: var(--fp-primary);
      color: var(--fp-white);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: background .15s, border-color .15s;
    }}
    .btn-submit:hover {{ background: var(--fp-primary-hover); border-color: var(--fp-primary-hover); }}
    .btn-submit .material-icons-round {{ font-size: 16px; }}

    /* ── Footer ── */
    footer {{
      text-align: center;
      padding: 20px;
      font-size: var(--fs-xs);
      color: var(--fp-secondary-50);
      flex-shrink: 0;
    }}
  </style>
</head>
<body>

<header>
  <div class="logo">
    <img src="/static/fairpicture.svg" alt="Fairpicture" />
    <div class="logo-divider"></div>
    <span class="logo-product">CAM — <span>Consent</span> Asset Matcher</span>
  </div>
</header>

<div class="stage">
  <div class="card">
    <div class="card-header">
      <div class="card-title">Sign in</div>
      <div class="card-sub">Internal tool · restricted access</div>
    </div>
    <div class="card-body">
      {error_block}
      <form method="post" action="/login">
        <div class="field">
          <label for="password">Password</label>
          <input id="password" name="password" type="password"
                 autocomplete="current-password" required autofocus
                 placeholder="Enter your password" />
        </div>
        <button class="btn-submit" type="submit">
          <span class="material-icons-round">lock_open</span>
          Sign in
        </button>
      </form>
    </div>
  </div>
</div>

<footer>Fairpicture · internal use only</footer>

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


@app.post("/api/test-relate")
def test_relate(body: dict):
    """
    Probe endpoint: try several strategies to link a document as a Related File
    on an image, then read the image back and return the full raw asset JSON.

    Body: { "image_id": "...", "document_id": "..." }
    """
    image_id = body.get("image_id")
    doc_id   = body.get("document_id")
    if not image_id or not doc_id:
        raise HTTPException(400, "image_id and document_id required")

    results = {}

    # ── Strategy 1: PUT relatedAssets array ──────────────────────────────────
    try:
        r = canto._request("PUT", f"{canto.BASE_URL}/api/v1/image/{image_id}",
            headers={**canto._headers(), "Content-Type": "application/json"},
            json={"relatedAssets": [{"id": doc_id, "scheme": "document"}]})
        results["strategy1_relatedAssets"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        results["strategy1_relatedAssets"] = {"error": str(e)}

    # ── Strategy 2: POST to /relatedAsset sub-resource ───────────────────────
    try:
        r = canto._request("POST", f"{canto.BASE_URL}/api/v1/image/{image_id}/relatedAsset",
            headers={**canto._headers(), "Content-Type": "application/json"},
            json={"id": doc_id, "scheme": "document"})
        results["strategy2_post_relatedAsset"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        results["strategy2_post_relatedAsset"] = {"error": str(e)}

    # ── Strategy 3: PUT additional.Consent (old approach, for reference) ─────
    try:
        r = canto._request("PUT", f"{canto.BASE_URL}/api/v1/image/{image_id}",
            headers={**canto._headers(), "Content-Type": "application/json"},
            json={"additional": {"Consent": doc_id}})
        results["strategy3_additional_consent"] = {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        results["strategy3_additional_consent"] = {"error": str(e)}

    # ── Read asset back to see resulting structure ────────────────────────────
    try:
        asset = canto.get_asset("image", image_id)
        results["asset_after"] = asset
    except Exception as e:
        results["asset_after"] = {"error": str(e)}

    return results


@app.get("/api/debug/{project_id}")
def debug_project(project_id: str):
    """Return raw Canto search results for images and documents (unfiltered)."""
    raw_images = canto.search_assets(keyword=project_id, scheme="image", limit=100)
    raw_docs   = canto.search_assets(keyword=project_id, scheme="document", limit=100)
    def slim(assets):
        return [{"id": a.get("id"), "name": a.get("name"),
                 "albums": [al.get("namePath") for al in a.get("relatedAlbums", [])]}
                for a in assets]
    return {
        "images": {"total": len(raw_images), "items": slim(raw_images)},
        "documents": {"total": len(raw_docs), "items": slim(raw_docs)},
    }


@app.get("/api/compliance")
def get_compliance():
    """Global compliance overview — reads Consent field from every image in Canto."""
    tree = canto.get_folder_tree()

    projects_out = []
    grand_total = 0
    grand_linked = 0

    for folder in tree:
        m = re.search(r"[_](\d{3,5})[_]", folder.get("name", ""))
        pid = m.group(1) if m else None

        # Find the Photos sub-album; fall back to searching by project ID keyword
        photos_album = next(
            (c for c in folder.get("children", []) if re.search(r"photo", c.get("name", ""), re.I)),
            None,
        )

        if photos_album:
            images = canto.get_album_images(photos_album["id"])
        elif pid:
            images = canto.get_project_images(pid)
        else:
            images = []

        total = len(images)
        linked = sum(
            1 for img in images
            if img.get("additional", {}).get("Consent") or
               img.get("metadata", {}).get("Consent")
        )

        grand_total += total
        grand_linked += linked

        if total > 0:
            projects_out.append({
                "id": folder["id"],
                "name": folder.get("name", ""),
                "project_id": pid,
                "total": total,
                "linked": linked,
                "pct": round(linked / total * 100) if total else 0,
            })

    # Sort by compliance % ascending (worst first)
    projects_out.sort(key=lambda p: p["pct"])

    return {
        "total": grand_total,
        "linked": grand_linked,
        "pct": round(grand_linked / grand_total * 100) if grand_total else 0,
        "projects": projects_out,
    }


@app.get("/api/matches/{project_id}")
def get_matches(project_id: str, album_id: str | None = None):
    """Run matching for a project and return scored pairs."""
    # Prefer direct album fetch (accurate) over keyword search (unreliable)
    if album_id:
        images = canto.get_album_images(album_id)
    else:
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
