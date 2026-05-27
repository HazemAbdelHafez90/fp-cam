"""
Multi-signal consent-to-image matching engine.

Signals (Sprint 1):
  - name     : PDF extracted name vs image 'Person Shown in the Image'  (35%)
  - project  : PDF Project-ID present in image album path               (20%)
  - location : PDF country/city vs image country/city metadata          (15%)

Signals (Sprint 4, not yet active):
  - caption  : AI image caption vs PDF notes/title                      (15%)
  - face     : Facial recognition PDF photo vs image                    (15%)

Returns a MatchResult with overall score + per-signal breakdown.
"""

import re
from dataclasses import dataclass, field
from rapidfuzz import fuzz

# Weights must sum to 100 across active signals
WEIGHTS = {
    "name":     35,
    "project":  20,
    "location": 15,
    # reserved but inactive:
    "caption":  15,
    "face":     15,
}

ACTIVE_SIGNALS = ["name", "project", "location"]
ACTIVE_WEIGHT_TOTAL = sum(WEIGHTS[s] for s in ACTIVE_SIGNALS)  # 70


@dataclass
class SignalScore:
    signal: str
    score: float        # 0–100
    weight: int
    detail: str         # human-readable explanation


@dataclass
class MatchResult:
    overall: float                          # 0–100 normalised across active signals
    signals: list[SignalScore] = field(default_factory=list)
    passed_project_gate: bool = True

    @property
    def tier(self) -> str:
        if self.overall >= 85:
            return "auto"
        if self.overall >= 60:
            return "review"
        return "skip"

    def breakdown(self) -> str:
        lines = [f"Overall: {self.overall:.0f}%  [{self.tier.upper()}]"]
        for s in self.signals:
            lines.append(f"  {s.signal:<10} {s.score:>5.0f}/100  (×{s.weight}%)  {s.detail}")
        return "\n".join(lines)


def _persons_from_image(image: dict) -> list[str]:
    """
    Parse 'Person Shown in the Image' into a clean list of names.

    Supported formats (single field value):
      • Newline-separated:       "Alice\nBob"
      • Semicolon-separated:     "Alice;Bob"
      • Comma-separated names:   "Grace Mambwe Chanda (31), Chileshe Mutale(25)"
      • Name + age via comma:    "Mulatu Lodebo,40"   → one person, age stripped
      • Role prefix:             "wife Almaz Tamiru,30"
    """
    raw = image.get("additional", {}).get("Person Shown in the Image") or ""
    if not raw:
        return []

    names = []
    for line in re.split(r"\n|;", raw):
        line = line.strip()
        if not line:
            continue
        # Remove role prefix words
        line = re.sub(
            r"^\b(wife|husband|son|daughter|father|mother|brother|sister|child)\b\s*",
            "", line, flags=re.IGNORECASE,
        ).strip()
        # Strip parenthetical ages/roles: "(31)", "(Priest)", etc.
        line = re.sub(r"\(.*?\)", "", line).strip()
        # Strip trailing ",<number>" age pattern: "Name,40" → "Name"
        line = re.sub(r",\s*\d+.*$", "", line).strip()

        # Split remaining commas as person separators.
        # After the above cleanup any comma left separates two distinct names.
        parts = [p.strip() for p in line.split(",") if p.strip()]
        names.extend(parts)

    # Fallback: if nothing parsed yet, try " and " or "+"
    if not names:
        for part in re.split(r"\band\b|\+", raw, flags=re.IGNORECASE):
            part = re.sub(r",\s*\d+.*$", "", part).strip()
            part = re.sub(r"\(.*?\)", "", part).strip()
            if part:
                names.append(part)

    return names


def _score_name(image: dict, pdf_data: dict) -> SignalScore:
    image_persons = _persons_from_image(image)
    pdf_names = [n for n in pdf_data.get("all_names", []) if n and n != "-"]

    if not image_persons:
        return SignalScore("name", 0, WEIGHTS["name"], "no 'Person Shown' on image")
    if not pdf_names:
        return SignalScore("name", 0, WEIGHTS["name"], "no name in PDF or filename")

    # Find the best-matching (image_person, pdf_name) pair
    best, best_pair = 0.0, ("", "")
    for img_p in image_persons:
        for pdf_n in pdf_names:
            s = fuzz.token_set_ratio(img_p.lower(), pdf_n.lower())
            if s > best:
                best, best_pair = s, (img_p, pdf_n)

    n_persons = len(image_persons)
    source_tag = " [from filename]" if pdf_data.get("name_source") == "filename" else ""
    detail = f"'{best_pair[0]}' ↔ '{best_pair[1]}'{source_tag}"
    if n_persons > 1:
        detail += f" ({n_persons} people in image)"
    return SignalScore("name", best, WEIGHTS["name"], detail)


def persons_from_image(image: dict) -> list[str]:
    """Public wrapper — used by the API to expose parsed person list."""
    return _persons_from_image(image)


def _score_project(image: dict, pdf_data: dict) -> SignalScore:
    pdf_pid = pdf_data.get("project_id", "").strip()
    if not pdf_pid:
        return SignalScore("project", 0, WEIGHTS["project"], "no Project-ID in PDF")

    image_albums = " ".join(a.get("namePath", "") for a in image.get("relatedAlbums", []))
    # Exact project number match (avoid 1319 matching 13190)
    pattern = rf"[_(./]{re.escape(pdf_pid)}[_)/.]"
    matched = bool(re.search(pattern, image_albums))
    score = 100.0 if matched else 0.0
    detail = f"project {pdf_pid} {'found' if matched else 'NOT found'} in albums"
    return SignalScore("project", score, WEIGHTS["project"], detail)


def _score_location(image: dict, pdf_data: dict) -> SignalScore:
    img_country = (image.get("additional", {}).get("Country") or "").lower().strip()
    img_city    = (image.get("additional", {}).get("City") or "").lower().strip()
    pdf_country = (pdf_data.get("country") or "").lower().strip()
    pdf_city    = (pdf_data.get("city") or pdf_data.get("production_location") or "").lower().strip()

    if not (img_country or img_city) or not (pdf_country or pdf_city):
        return SignalScore("location", 0, WEIGHTS["location"], "location data missing on image or PDF")

    country_score = fuzz.token_set_ratio(img_country, pdf_country) if img_country and pdf_country else 0
    city_score    = fuzz.token_set_ratio(img_city, pdf_city)       if img_city    and pdf_city    else 0

    # Weight country more heavily than city
    combined = (country_score * 0.6 + city_score * 0.4) if (country_score or city_score) else 0
    detail = f"country: '{img_country}' ↔ '{pdf_country}' ({country_score:.0f})  city: '{img_city}' ↔ '{pdf_city}' ({city_score:.0f})"
    return SignalScore("location", combined, WEIGHTS["location"], detail)


def score_match(image: dict, pdf_data: dict) -> MatchResult:
    """Score an image ↔ PDF pair across all active signals."""
    project_signal = _score_project(image, pdf_data)

    # Hard gate: if project IDs are both present and don't match, score is 0
    pdf_pid = pdf_data.get("project_id", "").strip()
    if pdf_pid and project_signal.score == 0:
        return MatchResult(
            overall=0.0,
            signals=[project_signal],
            passed_project_gate=False,
        )

    name_signal     = _score_name(image, pdf_data)
    location_signal = _score_location(image, pdf_data)

    signals = [name_signal, project_signal, location_signal]

    # Weighted average normalised to active signal weight total (70)
    weighted_sum = sum(s.score * s.weight for s in signals)
    overall = weighted_sum / ACTIVE_WEIGHT_TOTAL

    return MatchResult(overall=overall, signals=signals, passed_project_gate=True)
