"""Canto API client."""

import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

CACHE_DIR = Path(".cache/pdfs")
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
    resp = requests.request(method, url, headers=_headers(), **kwargs)
    if resp.status_code == 401:
        _tm.invalidate()
        resp = requests.request(method, url, headers=_headers(), **kwargs)
    return resp


def get_folder_tree() -> list[dict]:
    resp = _request("GET", f"{BASE_URL}/api/v1/tree")
    resp.raise_for_status()
    return resp.json().get("results", [])


def search_assets(keyword: str, scheme: str = "", limit: int = 100) -> list[dict]:
    params = {"keyword": keyword, "limit": limit, "start": 0}
    if scheme:
        params["scheme"] = scheme
    results = []
    while True:
        resp = _request("GET", f"{BASE_URL}/api/v1/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        found = data.get("results", [])
        results.extend(found)
        if len(results) >= (data.get("found") or 0) or not found:
            break
        params["start"] += limit
    return results


def get_project_documents(project_id: str) -> list[dict]:
    results = search_assets(keyword=project_id, scheme="document", limit=100)
    return [r for r in results if
            any(f"_{project_id}_" in a.get("namePath", "") or
                f"/{project_id}_" in a.get("namePath", "") or
                f"({project_id})" in r.get("name", "")
                for a in r.get("relatedAlbums", []))]


def get_project_images(project_id: str) -> list[dict]:
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


def update_consent_field(image_id: str, pdf_id: str) -> bool:
    headers = {**_headers(), "Content-Type": "application/json"}
    resp = requests.put(
        f"{BASE_URL}/api/v1/image/{image_id}",
        headers=headers,
        json={"additional": {"Consent": pdf_id}},
    )
    if resp.status_code == 401:
        _tm.invalidate()
        headers = {**_headers(), "Content-Type": "application/json"}
        resp = requests.put(
            f"{BASE_URL}/api/v1/image/{image_id}",
            headers=headers,
            json={"additional": {"Consent": pdf_id}},
        )
    return resp.status_code in (200, 201, 204)
