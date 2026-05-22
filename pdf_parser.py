"""Extract structured fields from Fairpicture consent PDFs."""

import io
import re
import pdfplumber


def parse_consent_pdf(pdf_bytes: bytes) -> dict:
    """
    Parse a Fairpicture consent PDF and return structured fields.
    Returns dict with: name, age, gender, project_id, project_title,
                       country, consent_date, organisation, notes
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
    all_names = re.findall(r"^Name:\s*(.+)$", text, re.MULTILINE)
    additional_names = [n.strip() for n in all_names[1:] if n.strip() and n.strip() != "-"]

    return {
        "name": name,
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
        "all_names": [name] + additional_names,
        "raw_text": text,
    }
