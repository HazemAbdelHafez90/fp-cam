"""CAM — Consent Asset Matcher API"""

import json
import os
import re
import requests
from datetime import datetime, timezone
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
from pdf_parser import parse_consent_pdf, name_from_filename
from matcher import score_match
import matcher

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

# ── Supabase decisions store ──────────────────────────────────────────────────
_SB_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_SB_KEY = os.getenv("SUPABASE_KEY", "")


def _sb_headers() -> dict:
    return {
        "apikey": _SB_KEY,
        "Authorization": f"Bearer {_SB_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }


def _sb_available() -> bool:
    return bool(_SB_URL and _SB_KEY)


def _load_decisions() -> dict:
    """Load all decisions from Supabase into a local dict cache."""
    if not _sb_available():
        # fallback: local file
        path = Path("/tmp/cam-decisions.json") if os.getenv("VERCEL") else Path(".cache/decisions.json")
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception:
            pass
        return {}
    try:
        import requests as _req
        r = _req.get(f"{_SB_URL}/rest/v1/decisions?select=*", headers=_sb_headers(), timeout=5)
        r.raise_for_status()
        return {row["image_id"]: {"action": row["action"], "pdf_id": row["pdf_id"]} for row in r.json()}
    except Exception:
        return {}


def _save_decision(image_id: str, record: dict) -> str | None:
    """Upsert a single decision to Supabase. Returns error string or None on success."""
    if not _sb_available():
        path = Path("/tmp/cam-decisions.json") if os.getenv("VERCEL") else Path(".cache/decisions.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(decisions))
        except Exception as e:
            return str(e)
        return None
    try:
        import requests as _req
        r = _req.post(
            f"{_SB_URL}/rest/v1/decisions",
            headers=_sb_headers(),
            json={
                "image_id":   image_id,
                "pdf_id":     record.get("pdf_id", ""),
                "pdf_name":   record.get("pdf_name", ""),
                "image_name": record.get("image_name", ""),
                "action":     record.get("action", ""),
                "project_id": record.get("project_id", ""),
            },
            timeout=5,
        )
        if not r.ok:
            return f"Supabase {r.status_code}: {r.text}"
        return None
    except Exception as e:
        return str(e)


decisions: dict[str, dict] = _load_decisions()


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_pdf_data(doc: dict) -> dict | None:
    url = doc.get("url", {}).get("directUrlOriginal", "")
    if not url:
        return None
    try:
        raw = canto.download_pdf_cached(doc["id"], url)
        data = parse_consent_pdf(raw, filename=doc.get("name", ""))
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
    """Return project folders that have a Photos sub-album (reliable signal of actual images)."""
    tree = canto.get_folder_tree()
    projects = []
    for folder in tree:
        children = folder.get("children", [])
        # Require at least one album whose name contains "photo" — empty or video-only
        # folders (e.g. Videos_1434, Documents_1434) are excluded.
        photos_albums = [c for c in children if re.search(r"photo", c.get("name", ""), re.I)]
        if not photos_albums:
            continue
        m = re.search(r"[_](\d{3,5})[_]", folder.get("name", ""))
        pid = m.group(1) if m else None
        projects.append({
            "id": folder["id"],
            "name": folder["name"],
            "project_id": pid,
            "albums": [{"id": c["id"], "name": c["name"]} for c in children],
        })
    return projects


def _consent_image_to_pdf_data(image: dict, project_id: str) -> dict | None:
    """
    Convert a scanned consent form image asset into a pdf_data-compatible dict
    so the matcher can treat it like a parsed PDF.

    Name is inferred from the image filename (e.g. 'Jafali_Nowa_consent.jpg'
    → 'Jafali Nowa'). Images whose filenames yield no usable name are skipped.
    """
    filename = image.get("name", "")
    inferred_name = name_from_filename(filename)
    if not inferred_name:
        return None

    url_obj  = image.get("url", {})
    detail   = url_obj.get("detail", "")

    return {
        "name":               inferred_name,
        "name_source":        "filename",
        "all_names":          [inferred_name],
        "additional_names":   [],
        "project_id":         project_id,
        "country":            "",
        "city":               "",
        "production_location": "",
        "age": "", "gender": "", "project_title": "",
        "consent_date": "", "organisation": "", "notes": "",
        "raw_text":           "",
        "_id":                image.get("id", ""),
        "_name":              filename,
        "_url":               detail,
        "_is_image_consent":  True,   # tells the UI to label this as "scanned form"
    }


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

    # ── /rest/related/create — the correct Canto Related Files endpoint ───────
    try:
        img  = canto.get_asset("image",    image_id)
        doc  = canto.get_asset("document", doc_id)
        success = canto.link_related_file(
            image_id,  img.get("name", image_id),
            doc_id,    doc.get("name", doc_id),
        )
        results["rest_related_create"] = {"success": success}
    except Exception as e:
        results["rest_related_create"] = {"error": str(e)}

    # ── Read asset back to see resulting structure ────────────────────────────
    try:
        asset = canto.get_asset("image", image_id)
        results["asset_after"] = asset
    except Exception as e:
        results["asset_after"] = {"error": str(e)}

    return results


@app.get("/api/debug/document/{doc_id}")
def debug_document(doc_id: str):
    """Return raw Canto document asset + all REST related endpoints for a document ID."""
    result = {}
    try:
        result["api_v1_document"] = canto.get_asset("document", doc_id)
    except Exception as e:
        result["api_v1_document"] = {"error": str(e)}
    # Try the REST related list endpoint
    for path in [
        f"/rest/related?id={doc_id}&scheme=document",
        f"/rest/related/list?id={doc_id}",
        f"/rest/related/{doc_id}",
    ]:
        try:
            from canto_client import _request, BASE_URL
            resp = _request("GET", f"{BASE_URL}{path}")
            result[path] = resp.json() if resp.ok else {"status": resp.status_code, "text": resp.text[:300]}
        except Exception as e:
            result[path] = {"error": str(e)}
    return result


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


_COMPLIANCE_TABLE = "project_compliance"
_MATCHES_TABLE    = "project_matches"


def _get_confirmed_image_ids(image_ids: list) -> set:
    """Return the set of image_ids that have a confirmed decision in Supabase."""
    if not _sb_available() or not image_ids:
        return set()
    ids_param = ",".join(image_ids)
    r = requests.get(
        f"{_SB_URL}/rest/v1/decisions?select=image_id&action=eq.confirmed"
        f"&image_id=in.({ids_param})",
        headers=_sb_headers(), timeout=10,
    )
    if not r.ok:
        return set()
    return {row["image_id"] for row in r.json()}


def _upsert_compliance_row(row: dict) -> str | None:
    """Upsert one row. Returns error string on failure, None on success."""
    if not _sb_available():
        return "Supabase not configured"
    r = requests.post(
        f"{_SB_URL}/rest/v1/{_COMPLIANCE_TABLE}",
        headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates"},
        json=row, timeout=10,
    )
    if not r.ok:
        return f"Supabase {r.status_code}: {r.text}"
    return None


@app.get("/api/compliance")
def get_compliance():
    """Read cached compliance data from Supabase project_compliance table."""
    if not _sb_available():
        return {"projects": [], "totals": {}, "error": "Supabase not configured"}
    r = requests.get(
        f"{_SB_URL}/rest/v1/{_COMPLIANCE_TABLE}?select=*&order=project_id.asc",
        headers=_sb_headers(), timeout=10,
    )
    if not r.ok:
        raise HTTPException(500, f"Supabase: {r.text}")
    rows = r.json()

    total_images     = sum(p.get("total_images") or 0 for p in rows)
    with_consent     = sum(p.get("with_consent") or 0 for p in rows)
    needs_consent    = sum(p.get("needs_consent") or 0 for p in rows)
    no_person        = sum(p.get("no_person") or 0 for p in rows)
    no_images        = sum(1 for p in rows if (p.get("total_images") or 0) == 0)
    persons_total    = sum(p.get("persons_total") or 0 for p in rows)
    persons_matched  = sum(p.get("persons_matched") or 0 for p in rows)
    partial_count    = sum(p.get("partial_count") or 0 for p in rows)
    orphan_forms     = sum(p.get("orphan_forms") or 0 for p in rows)

    tracked_images = with_consent + needs_consent
    compliance_pct = round(with_consent / tracked_images * 100, 1) if tracked_images else 0

    return {
        "projects": rows,
        "totals": {
            "total_images":    total_images,
            "with_consent":    with_consent,
            "needs_consent":   needs_consent,
            "no_person":       no_person,
            "no_images":       no_images,
            "persons_total":   persons_total,
            "persons_matched": persons_matched,
            "partial_count":   partial_count,
            "orphan_forms":    orphan_forms,
            "compliance_pct":  compliance_pct,
        },
    }


def _fetch_pdf_data_for_project(project_id: str, docs_album_id: str = "",
                                consent_album_id: str = "") -> list[dict]:
    """Fetch and parse all consent PDFs + scanned images for a project."""
    if docs_album_id:
        docs = canto.get_album_documents(docs_album_id)
        if not docs:
            docs = canto.get_project_documents(project_id)
    else:
        docs = canto.get_project_documents(project_id)

    pdf_data_list = [d for d in (_load_pdf_data(doc) for doc in docs
                                  if doc.get("name", "").lower().endswith(".pdf"))
                     if d is not None]

    if consent_album_id:
        raw_imgs = canto.get_album_images(consent_album_id)
        pdf_data_list.extend(
            d for d in (_consent_image_to_pdf_data(img, project_id) for img in raw_imgs)
            if d is not None
        )

    return pdf_data_list


@app.post("/api/compliance/scan/{folder_id}")
def scan_project_compliance(folder_id: str, body: dict):
    """Scan one project folder and save results to Supabase. Idempotent — safe to retry."""
    project_name      = body.get("project_name", "")
    project_id        = body.get("project_id", "")
    album_id          = body.get("album_id", "")
    docs_album_id     = body.get("docs_album_id", "")
    consent_album_id  = body.get("consent_album_id", "")

    try:
        if album_id:
            images = canto.get_album_images(album_id)
        elif project_id:
            images = canto.get_project_images(project_id)
        else:
            images = []

        image_ids     = [img["id"] for img in images]
        confirmed_ids = _get_confirmed_image_ids(image_ids)

        pdf_data_list = _fetch_pdf_data_for_project(
            project_id, docs_album_id, consent_album_id
        ) if project_id else []

        matched_pdf_ids: set[str] = set()

        with_consent = needs_consent = no_person = 0
        persons_total = persons_matched = partial_count = 0

        for img in images:
            persons = matcher.persons_from_image(img)

            if not persons:
                no_person += 1
                continue

            if img["id"] in confirmed_ids:
                with_consent += 1
                persons_total   += len(persons)
                persons_matched += len(persons)
                continue

            if not pdf_data_list:
                needs_consent += 1
                persons_total += len(persons)
                continue

            n_matched = 0
            for person in persons:
                persons_total += 1
                pseudo_img = {**img, "additional": {
                    **img.get("additional", {}),
                    "Person Shown in the Image": person,
                }}
                best_score = 0.0
                best_pdf_id = None
                for pdf_data in pdf_data_list:
                    result = score_match(pseudo_img, pdf_data)
                    if result.overall > best_score:
                        best_score = result.overall
                        best_pdf_id = pdf_data["_id"]

                if best_score >= 60:
                    n_matched += 1
                    persons_matched += 1
                    if best_pdf_id:
                        matched_pdf_ids.add(best_pdf_id)

            if n_matched == len(persons):
                with_consent += 1
            elif n_matched > 0:
                partial_count += 1
                needs_consent += 1
            else:
                needs_consent += 1

        orphan_forms = sum(
            1 for pdf in pdf_data_list if pdf["_id"] not in matched_pdf_ids
        ) if pdf_data_list else 0

        row = {
            "folder_id":       folder_id,
            "project_name":    project_name,
            "project_id":      project_id or "",
            "total_images":    len(images),
            "with_consent":    with_consent,
            "needs_consent":   needs_consent,
            "no_person":       no_person,
            "persons_total":   persons_total,
            "persons_matched": persons_matched,
            "partial_count":   partial_count,
            "orphan_forms":    orphan_forms,
            "status":          "done",
            "scanned_at":      datetime.now(timezone.utc).isoformat(),
            "error_msg":       None,
        }
        db_error = _upsert_compliance_row(row)
        return {**row, "db_error": db_error}

    except Exception as e:
        _upsert_compliance_row({
            "folder_id":    folder_id,
            "project_name": project_name,
            "project_id":   project_id or "",
            "status":       "error",
            "error_msg":    str(e),
        })
        raise HTTPException(500, str(e))


@app.get("/api/compliance/status")
def compliance_status():
    """Check whether Supabase is reachable and the project_compliance table exists."""
    if not _sb_available():
        return {"ok": False, "error": "SUPABASE_URL or SUPABASE_KEY not set"}
    r = requests.get(
        f"{_SB_URL}/rest/v1/{_COMPLIANCE_TABLE}?select=folder_id&limit=1",
        headers=_sb_headers(), timeout=8,
    )
    if r.status_code == 404 or (not r.ok and "does not exist" in r.text):
        return {
            "ok": False,
            "error": "Table 'project_compliance' not found. Run this SQL in Supabase:",
            "sql": (
                "create table project_compliance (\n"
                "  folder_id       text primary key,\n"
                "  project_name    text,\n"
                "  project_id      text,\n"
                "  total_images    int  default 0,\n"
                "  with_consent    int  default 0,\n"
                "  needs_consent   int  default 0,\n"
                "  no_person       int  default 0,\n"
                "  persons_total   int  default 0,\n"
                "  persons_matched int  default 0,\n"
                "  partial_count   int  default 0,\n"
                "  orphan_forms    int  default 0,\n"
                "  status          text default 'pending',\n"
                "  scanned_at      timestamptz,\n"
                "  error_msg       text\n"
                ");\n"
                "alter table project_compliance disable row level security;\n\n"
                "create table if not exists project_matches (\n"
                "  project_id      text primary key,\n"
                "  folder_id       text,\n"
                "  project_name    text,\n"
                "  results_json    jsonb,\n"
                "  persons_total   int  default 0,\n"
                "  persons_matched int  default 0,\n"
                "  partial_count   int  default 0,\n"
                "  orphan_forms    int  default 0,\n"
                "  status          text default 'pending',\n"
                "  scanned_at      timestamptz,\n"
                "  error_msg       text\n"
                ");\n"
                "alter table project_matches disable row level security;"
            ),
        }
    if not r.ok:
        return {"ok": False, "error": f"Supabase {r.status_code}: {r.text}"}

    # Check that new columns exist (migration applied)
    r2 = requests.get(
        f"{_SB_URL}/rest/v1/{_COMPLIANCE_TABLE}?select=persons_total&limit=1",
        headers=_sb_headers(), timeout=8,
    )
    if not r2.ok:
        return {
            "ok": False,
            "error": "Schema migration needed. Run this SQL in Supabase:",
            "sql": (
                "alter table project_compliance\n"
                "  add column if not exists persons_total   int default 0,\n"
                "  add column if not exists persons_matched int default 0,\n"
                "  add column if not exists partial_count   int default 0,\n"
                "  add column if not exists orphan_forms    int default 0;\n\n"
                "create table if not exists project_matches (\n"
                "  project_id      text primary key,\n"
                "  folder_id       text,\n"
                "  project_name    text,\n"
                "  results_json    jsonb,\n"
                "  persons_total   int  default 0,\n"
                "  persons_matched int  default 0,\n"
                "  partial_count   int  default 0,\n"
                "  orphan_forms    int  default 0,\n"
                "  status          text default 'pending',\n"
                "  scanned_at      timestamptz,\n"
                "  error_msg       text\n"
                ");\n"
                "alter table project_matches disable row level security;"
            ),
        }

    return {"ok": True, "row_count": len(r.json())}


@app.post("/api/compliance/reset")
def reset_compliance():
    """Delete all cached rows in both tables so the next scan starts from scratch."""
    if not _sb_available():
        raise HTTPException(503, "Supabase not configured")
    r1 = requests.delete(
        f"{_SB_URL}/rest/v1/{_COMPLIANCE_TABLE}?folder_id=neq.''",
        headers={**_sb_headers(), "Prefer": "return=minimal"}, timeout=10,
    )
    r2 = requests.delete(
        f"{_SB_URL}/rest/v1/{_MATCHES_TABLE}?project_id=neq.''",
        headers={**_sb_headers(), "Prefer": "return=minimal"}, timeout=10,
    )
    return {"ok": r1.ok and r2.ok, "compliance": r1.status_code, "matches": r2.status_code}


def _upsert_matches_row(row: dict) -> str | None:
    """Upsert one row into project_matches. Returns error string on failure."""
    if not _sb_available():
        return "Supabase not configured"
    r = requests.post(
        f"{_SB_URL}/rest/v1/{_MATCHES_TABLE}",
        headers={**_sb_headers(), "Prefer": "resolution=merge-duplicates"},
        json=row, timeout=10,
    )
    if not r.ok:
        return f"Supabase {r.status_code}: {r.text}"
    return None


def _build_match_response(project_id: str, results: list, decisions: dict) -> dict:
    """Compute summary stats and return the standard matches response dict."""
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
    unresolvable     = sum(1 for r in results if r["person_shown"] and
                           (not r.get("best_match") or r["best_match"]["score"] == 0))

    persons_total   = sum(len(r["persons"]) for r in results if r["persons"])
    persons_matched = sum(
        sum(1 for p in r["persons"]
            if r.get("best_match") and r["best_match"]["score"] >= 60)
        for r in results if r["persons"]
    )
    partial_count   = sum(
        1 for r in results
        if r["persons"] and r.get("best_match") and 0 < r["best_match"]["score"] < 60
    )

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
            "persons_total":    persons_total,
            "persons_matched":  persons_matched,
            "partial_count":    partial_count,
            "orphan_forms":     0,
            "compliance":       round(confirmed / total * 100, 1) if total else 0,
        },
        "matches": results,
    }


@app.get("/api/matches/{project_id}/cached")
def get_matches_cached(project_id: str):
    """Return cached match results from Supabase. Returns 404 if not yet scanned."""
    if not _sb_available():
        raise HTTPException(503, "Supabase not configured")
    r = requests.get(
        f"{_SB_URL}/rest/v1/{_MATCHES_TABLE}"
        f"?project_id=eq.{project_id}&select=*&limit=1",
        headers=_sb_headers(), timeout=10,
    )
    if not r.ok:
        raise HTTPException(500, f"Supabase: {r.text}")
    rows = r.json()
    if not rows:
        raise HTTPException(404, "No cached results — run a scan first")
    row = rows[0]
    if row.get("status") == "error":
        raise HTTPException(500, row.get("error_msg", "Scan failed"))

    stored = row.get("results_json") or {}
    if isinstance(stored, list):
        matches      = stored
        orphan_forms = []
    else:
        matches      = stored.get("matches", [])
        orphan_forms = stored.get("orphan_forms", [])

    return {
        "project_id":    project_id,
        "scanned_at":    row.get("scanned_at"),
        "stats": {
            "persons_total":   row.get("persons_total", 0),
            "persons_matched": row.get("persons_matched", 0),
            "partial_count":   row.get("partial_count", 0),
            "orphan_forms":    row.get("orphan_forms", 0),
        },
        "matches":       matches,
        "orphan_forms":  orphan_forms,
    }


@app.post("/api/matches/{project_id}/scan")
def scan_matches(project_id: str, body: dict):
    """Run matching, persist to Supabase cache, return response. Idempotent."""
    album_id         = body.get("album_id", "")
    docs_album_id    = body.get("docs_album_id", "")
    consent_album_id = body.get("consent_album_id", "")
    folder_id        = body.get("folder_id", project_id)
    project_name     = body.get("project_name", "")

    try:
        response = _run_matching(project_id, album_id, docs_album_id, consent_album_id)
        results_for_storage = response["matches"]
        stats = response["stats"]

        # A PDF is "matched" if the algorithm chose it as best for any photo,
        # OR if an operator manually confirmed it via CAM decisions.
        matched_pdf_ids = {
            r["best_match"]["pdf_id"]
            for r in results_for_storage
            if r.get("best_match") and r["best_match"].get("pdf_id")
        }
        for r in results_for_storage:
            did = r.get("decided_pdf_id")
            if did:
                matched_pdf_ids.add(did)
        pdf_candidate_map: dict[str, dict] = {}
        for r in results_for_storage:
            for c in r.get("candidates", []):
                pid_c = c.get("pdf_id")
                if not pid_c:
                    continue
                existing = pdf_candidate_map.get(pid_c)
                if existing is None or c["score"] > existing["best_score"]:
                    pdf_candidate_map[pid_c] = {
                        "pdf_id":           pid_c,
                        "pdf_name":         c.get("pdf_name", ""),
                        "pdf_url":          c.get("pdf_url", ""),
                        "is_image_consent": c.get("is_image_consent", False),
                        "best_score":       c["score"],
                        "best_image_id":       r["image_id"],
                        "best_image_name":     r["image_name"],
                        "best_image_thumb":    r.get("image_thumb", ""),
                        "best_image_canto_url": r.get("image_canto_url", ""),
                    }

        # Candidates not matched by algorithm or decision
        candidate_orphans = [v for k, v in pdf_candidate_map.items() if k not in matched_pdf_ids]

        # Fetch relatedFile once per candidate PDF (single source of truth).
        # Album image listings don't include relatedFile, so we read from the PDF side.
        all_candidate_pdf_ids = {
            c.get("pdf_id")
            for r in results_for_storage
            for c in r.get("candidates", [])
            if c.get("pdf_id") and not c.get("is_image_consent")
        }
        pdf_related_images: dict[str, set[str]] = {
            pdf_id: canto.get_document_related_image_ids(pdf_id)
            for pdf_id in all_candidate_pdf_ids
        }

        # Build image_id → linked_doc_count by inverting the map
        image_linked_count: dict[str, int] = {}
        for img_ids in pdf_related_images.values():
            for img_id in img_ids:
                image_linked_count[img_id] = image_linked_count.get(img_id, 0) + 1

        # Patch linked_consent_count into every result
        for r in results_for_storage:
            r["linked_consent_count"] = image_linked_count.get(r["image_id"], 0)

        # A PDF is truly orphaned if:
        #   - not algorithmically matched (not in matched_pdf_ids), AND
        #   - has no related image in Canto (pdf_related_images empty or absent)
        orphan_list = sorted(
            [v for v in candidate_orphans
             if not pdf_related_images.get(v["pdf_id"])],
            key=lambda x: x["best_score"], reverse=True,
        )

        scanned_at = datetime.now(timezone.utc).isoformat()
        _upsert_matches_row({
            "project_id":      project_id,
            "folder_id":       folder_id,
            "project_name":    project_name,
            "results_json":    {"matches": results_for_storage, "orphan_forms": orphan_list},
            "persons_total":   stats.get("persons_total", 0),
            "persons_matched": stats.get("persons_matched", 0),
            "partial_count":   stats.get("partial_count", 0),
            "orphan_forms":    len(orphan_list),
            "status":          "done",
            "scanned_at":      scanned_at,
            "error_msg":       None,
        })

        return {**response, "orphan_forms": orphan_list, "scanned_at": scanned_at}

    except HTTPException:
        raise
    except Exception as e:
        _upsert_matches_row({
            "project_id":   project_id,
            "folder_id":    folder_id,
            "project_name": project_name,
            "status":       "error",
            "error_msg":    str(e),
        })
        raise HTTPException(500, str(e))


def _run_matching(project_id: str, album_id: str = "",
                  docs_album_id: str = "", consent_album_id: str = "") -> dict:
    """Core matching logic — shared by scan endpoint and legacy get_matches."""
    global decisions
    decisions = _load_decisions()

    if album_id:
        images = canto.get_album_images(album_id)
    else:
        images = canto.get_project_images(project_id)

    pdf_data_list = _fetch_pdf_data_for_project(project_id, docs_album_id, consent_album_id)

    if not images:
        raise HTTPException(404, f"No images found for project {project_id}")
    if not pdf_data_list:
        raise HTTPException(404, f"No consent documents or scanned forms found for project {project_id}")

    results = []
    for image in images:
        best_result, best_pdf = None, None
        all_candidates = []

        for pdf_data in pdf_data_list:
            result = score_match(image, pdf_data)
            all_candidates.append({
                "pdf_id":            pdf_data["_id"],
                "pdf_name":          pdf_data["_name"],
                "pdf_url":           pdf_data["_url"],
                "pdf_names":         pdf_data.get("all_names", []),
                "is_image_consent":  pdf_data.get("_is_image_consent", False),
                "score":             round(result.overall, 1),
                "tier":              result.tier,
                "signals": {s.signal: {"score": round(s.score, 1), "detail": s.detail}
                            for s in result.signals},
            })
            if best_result is None or result.overall > best_result.overall:
                best_result, best_pdf = result, pdf_data

        all_candidates.sort(key=lambda c: c["score"], reverse=True)
        decision = decisions.get(image["id"], {})
        img_additional = image.get("additional", {})

        results.append({
            "image_id":        image["id"],
            "image_name":      image.get("name", ""),
            "image_thumb":     image.get("url", {}).get("directUrlPreview", "") or image.get("url", {}).get("previewURI240", ""),
            "image_canto_url": image.get("url", {}).get("detail", ""),
            "person_shown":    img_additional.get("Person Shown in the Image") or "",
            "persons":         matcher.persons_from_image(image),
            "country":         img_additional.get("Country") or "",
            "city":            img_additional.get("City") or "",
            "consent_linked":       img_additional.get("Consent") or "",
            "linked_consent_count": 0,  # populated by _run_scan_and_cache via PDF-side relatedFile
            "project_id":      project_id,
            "best_match": {
                "pdf_id":           best_pdf["_id"]   if best_pdf else None,
                "pdf_name":         best_pdf["_name"] if best_pdf else None,
                "pdf_url":          best_pdf["_url"]  if best_pdf else None,
                "is_image_consent": best_pdf.get("_is_image_consent", False) if best_pdf else False,
                "score":            round(best_result.overall, 1) if best_result else 0,
                "tier":             best_result.tier if best_result else "skip",
                "signals":          {s.signal: {"score": round(s.score, 1), "detail": s.detail}
                                     for s in best_result.signals} if best_result else {},
            } if best_pdf else None,
            "candidates":      all_candidates[:5],
            "decision":        decision.get("action", ""),
            "decided_pdf_id":  decision.get("pdf_id", ""),
        })

    return _build_match_response(project_id, results, decisions)


@app.get("/api/matches/{project_id}")
def get_matches(
    project_id: str,
    album_id: str | None = None,
    docs_album_id: str | None = None,
    consent_album_id: str | None = None,
):
    """Run matching live (no cache). Kept for backwards compatibility.

    Prefer POST /api/matches/{project_id}/scan for cached runs.
    """
    return _run_matching(project_id, album_id or "", docs_album_id or "", consent_album_id or "")


class DecisionRequest(BaseModel):
    image_id:   str
    image_name: str = ""
    pdf_id:     str
    pdf_name:   str = ""
    action:     str   # "confirmed" | "rejected"


@app.post("/api/decision")
def set_decision(req: DecisionRequest):
    """Record a confirm or reject decision. On confirm, links assets via Related Files."""
    if req.action not in ("confirmed", "rejected"):
        raise HTTPException(400, "action must be 'confirmed' or 'rejected'")

    record = {
        "action":     req.action,
        "pdf_id":     req.pdf_id,
        "pdf_name":   req.pdf_name,
        "image_name": req.image_name,
    }
    decisions[req.image_id] = record
    db_error = _save_decision(req.image_id, record)

    if req.action == "confirmed":
        success = canto.link_related_file(
            req.image_id, req.image_name,
            req.pdf_id,   req.pdf_name,
        )
        return {"status": "ok", "linked_in_canto": success, "db_error": db_error}

    return {"status": "ok", "linked_in_canto": False, "db_error": db_error}


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
