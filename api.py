"""CAM — Consent Asset Matcher API"""

import os
import re
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

import canto_client as canto
from pdf_parser import parse_consent_pdf
from matcher import score_match

load_dotenv()

app = FastAPI(title="CAM — Consent Asset Matcher")

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

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")
