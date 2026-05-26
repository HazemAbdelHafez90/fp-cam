"""Canto API client."""

import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# /tmp is the only writable directory on serverless platforms (Vercel, etc.)
# Locally .cache/pdfs is used for persistence across restarts.
_default_cache = Path("/tmp/cam-pdfs") if os.getenv("VERCEL") else Path(".cache/pdfs")
CACHE_DIR = Path(os.getenv("PDF_CACHE_DIR", str(_default_cache)))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv()

BASE_URL = os.getenv("CANTO_BASE_URL", "https://fairpicture.canto.global").rstrip("/")
_OAUTH_URL = "https://oauth.canto.global/oauth/api/oauth2/token"


class _TokenManager:
    def __init__(self):
        self._token: str = ""
        self._expires_at: float = 0.0

    def _fetch(self) -> str:
        app_id = os.getenv("CANTO_APP_ID", "")
        app_secret = os.getenv("CANTO_APP_SECRET", "")
        if not app_id or not app_secret:
            raise RuntimeError("CANTO_APP_ID and CANTO_APP_SECRET must be set in environment")
        resp = requests.post(
            _OAUTH_URL,
            data={"app_id": app_id, "app_secret": app_secret, "grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["accessToken"]
        # expiresIn is in seconds; refresh 60s early
        self._expires_at = time.time() + int(data.get("expiresIn", 3600)) - 60
        return self._token

    def get(self) -> str:
        if not self._token or time.time() >= self._expires_at:
            self._fetch()
        return self._token

    def invalidate(self):
        """Force a refresh on the next get() call (e.g. after a 401)."""
        self._expires_at = 0.0


_tm = _TokenManager()


def _headers() -> dict:
    return {"Authorization": f"Bearer {_tm.get()}"}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP request with automatic token refresh on 401."""
    # Merge caller-supplied headers with auth header (auth wins on conflict)
    extra = kwargs.pop("headers", {})
    headers = {**extra, **_headers()}
    resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code == 401:
        _tm.invalidate()
        headers = {**extra, **_headers()}
        resp = requests.request(method, url, headers=headers, **kwargs)
    return resp


def get_folder_tree() -> list[dict]:
    resp = _request("GET", f"{BASE_URL}/api/v1/tree")
    resp.raise_for_status()
    return resp.json().get("results", [])


def search_assets(keyword: str, scheme: str = "", limit: int = 100, max_results: int = 500) -> list[dict]:
    params = {"keyword": keyword, "limit": limit, "start": 0}
    if scheme:
        params["scheme"] = scheme
    results = []
    while len(results) < max_results:
        resp = _request("GET", f"{BASE_URL}/api/v1/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        page = data.get("results", [])
        results.extend(page)
        total_found = data.get("found") or 0
        if not page or len(results) >= total_found:
            break
        params["start"] += limit
    return results


def get_album_documents(album_id: str) -> list[dict]:
    """Fetch documents from a specific Canto album (e.g. the Documents_NNNN sub-album)."""
    params = {"sortBy": "time", "sortDirection": "descending", "limit": 100, "start": 0,
              "scheme": "document"}
    results = []
    while len(results) < 500:
        resp = _request("GET", f"{BASE_URL}/api/v1/album/{album_id}", params=params)
        if not resp.ok:
            break
        data = resp.json()
        page = data.get("results", [])
        results.extend(page)
        total_found = data.get("found") or 0
        if not page or len(results) >= total_found:
            break
        params["start"] += len(page)
    return results


def get_project_documents(project_id: str) -> list[dict]:
    results = search_assets(keyword=project_id, scheme="document", limit=100)
    return [r for r in results if
            f"({project_id})" in r.get("name", "") or
            any(f"_{project_id}_" in a.get("namePath", "") or
                f"/{project_id}_" in a.get("namePath", "")
                for a in r.get("relatedAlbums", []))]


_MAX_IMAGES_PER_ALBUM = 500  # safety cap — no project should exceed this


def get_album_images(album_id: str) -> list[dict]:
    """Fetch images from a Canto album via the album content endpoint."""
    params = {"sortBy": "time", "sortDirection": "descending", "limit": 100, "start": 0,
              "scheme": "image"}
    results = []
    while len(results) < _MAX_IMAGES_PER_ALBUM:
        resp = _request("GET", f"{BASE_URL}/api/v1/album/{album_id}", params=params)
        if not resp.ok:
            return _search_album_images(album_id)
        data = resp.json()
        page = data.get("results", [])
        results.extend(page)
        total_found = data.get("found") or 0
        if not page or len(results) >= total_found:
            break
        params["start"] += len(page)
    # If results look like album metadata (no scheme key), fall back
    if results and "scheme" not in results[0]:
        return _search_album_images(album_id)
    return results


def _search_album_images(album_id: str) -> list[dict]:
    """Search images scoped to a specific album ID (fallback)."""
    params = {"scheme": "image", "limit": 100, "start": 0, "albums": album_id}
    results = []
    while len(results) < _MAX_IMAGES_PER_ALBUM:
        resp = _request("GET", f"{BASE_URL}/api/v1/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        page = data.get("results", [])
        results.extend(page)
        total_found = data.get("found") or 0
        if not page or len(results) >= total_found:
            break
        params["start"] += len(page)
    return results


def get_project_images(project_id: str) -> list[dict]:
    """Fallback: keyword search for images (less reliable than get_album_images)."""
    results = search_assets(keyword=project_id, scheme="image", limit=100)
    return [r for r in results if
            any(f"_{project_id}_" in a.get("namePath", "") or
                f"/{project_id}_" in a.get("namePath", "")
                for a in r.get("relatedAlbums", []))]


def download_pdf_cached(asset_id: str, url: str) -> bytes:
    cache_file = CACHE_DIR / f"{asset_id}.pdf"
    if cache_file.exists():
        return cache_file.read_bytes()
    resp = _request("GET", url)
    resp.raise_for_status()
    cache_file.write_bytes(resp.content)
    return resp.content


def clear_cache():
    for f in CACHE_DIR.glob("*.pdf"):
        f.unlink()


def get_asset(scheme: str, asset_id: str) -> dict:
    resp = _request("GET", f"{BASE_URL}/api/v1/{scheme}/{asset_id}")
    resp.raise_for_status()
    return resp.json()


def link_related_file(image_id: str, image_name: str, doc_id: str, doc_name: str) -> bool:
    """Link a consent PDF to an image via Canto's Related Files feature."""
    payload = {
        "relatedName": "",
        "relatedContents": [
            {"id": image_id, "scheme": "image",    "displayName": image_name},
            {"id": doc_id,   "scheme": "document", "displayName": doc_name},
        ],
    }
    resp = _request("POST", f"{BASE_URL}/rest/related/create", json=payload)
    return resp.status_code == 200


def get_document_related_image_ids(doc_id: str) -> set[str]:
    """Return the set of image IDs linked to a document via Canto Related Files."""
    try:
        resp = _request("GET", f"{BASE_URL}/api/v1/document/{doc_id}")
        if not resp.ok:
            return set()
        data = resp.json()
        # Canto stores related items under different keys depending on version
        related = (
            data.get("relatedContents") or
            data.get("related") or
            data.get("relatedFiles") or
            []
        )
        return {item["id"] for item in related if item.get("scheme") == "image"}
    except Exception:
        return set()


def update_consent_field(image_id: str, pdf_id: str) -> bool:
    """Legacy — kept for reference but writes don't work via API v1."""
    return False
