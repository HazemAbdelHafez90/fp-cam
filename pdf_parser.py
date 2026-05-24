"""Extract structured fields from Fairpicture consent PDFs."""

import io
import re
import pdfplumber

# Words to strip when inferring a name from a filename
_FILENAME_NOISE = re.compile(
    r"\b(consent|release|form|signed|final|scan|copy|"
    r"new|old|updated|v\d+|\d{4,})\b",
    re.IGNORECASE,
)


def name_from_filename(filename: str) -> str:
    """Public alias — use this outside pdf_parser."""
    return _name_from_filename(filename)


def _name_from_filename(filename: str) -> str:
    """
    Attempt to infer a person's name from a consent PDF filename.

    Examples:
      nyaruach_dhil_consent.pdf  → "Nyaruach Dhil"
      John_Doe_release_form.pdf  → "John Doe"
      consent_mulatu_lodebo.pdf  → "Mulatu Lodebo"
      IMG_1234.pdf               → ""   (no usable name)
    """
    stem = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    # Normalise separators to spaces
    stem = re.sub(r"[_\-\.]", " ", stem).strip()
    # Remove noise words
    stem = _FILENAME_NOISE.sub("", stem).strip()
    # Remove leftover numbers and punctuation tokens
    tokens = [t for t in stem.split() if re.search(r"[a-zA-Z]{2,}", t)]
    if len(tokens) < 2:
        return ""   # single token is not a full name
    # Title-case and join
    return " ".join(t.capitalize() for t in tokens)


def parse_consent_pdf(pdf_bytes: bytes, filename: str = "") -> dict:
    """
    Parse a Fairpicture consent PDF and return structured fields.
    Returns dict with: name, age, gender, project_id, project_title,
                       country, consent_date, organisation, notes

    If the PDF text contains no Name field, falls back to inferring the
    name from the filename (e.g. 'nyaruach_dhil_consent.pdf' → 'Nyaruach Dhil').
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    def extract(label: str) -> str:
        # Match "Label: value" — value ends at next newline
        m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    # Primary subject
    name = extract("Name")
    age = extract("Age")
    gender = extract("Gender")
    project_id = extract("Project-ID") or extract("Project ID")
    project_title = extract("Project title") or extract("Project Title")
    country = extract("Country")
    city = extract("City")
    production_location = extract("Production location")
    consent_date = extract("Date of consent")
    organisation = extract("Organisation name")
    notes = extract("Notes")

    # Also grab any additional "Name:" entries (people consented on behalf of)
    all_names_raw = re.findall(r"^Name:\s*(.+)$", text, re.MULTILINE)
    additional_names = [n.strip() for n in all_names_raw[1:] if n.strip() and n.strip() != "-"]

    # Filename fallback: if no name was found in the PDF text, try to infer
    # it from the filename (e.g. "nyaruach_dhil_consent.pdf" → "Nyaruach Dhil")
    name_source = "pdf"
    if not name and filename:
        inferred = _name_from_filename(filename)
        if inferred:
            name = inferred
            name_source = "filename"

    return {
        "name": name,
        "name_source": name_source,      # "pdf" or "filename" — useful for debugging
        "age": age,
        "gender": gender,
        "project_id": project_id,
        "project_title": project_title,
        "country": country,
        "city": city,
        "production_location": production_location,
        "consent_date": consent_date,
        "organisation": organisation,
        "notes": notes,
        "additional_names": additional_names,
        "all_names": [name] + additional_names if name else additional_names,
        "raw_text": text,
    }
