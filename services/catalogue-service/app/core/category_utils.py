import re

from sqlalchemy.orm import Session

from app import models


def generate_short_term(db: Session, category_name: str) -> str:
    """
    A reasonable default short term for a newly auto-registered category —
    initials for a multi-word name (e.g. "Computer Accessories" -> "CA"),
    or the first 3 letters for a single word (e.g. "Server" -> "SER").
    Numbered suffix appended if that collides with an existing one, since
    short_term must stay unique. Editable afterward from the Category list
    if the auto-generated one isn't what you'd have picked.
    """
    words = re.findall(r"[A-Za-z0-9]+", category_name)
    if len(words) >= 2:
        base = "".join(w[0] for w in words[:4]).upper()
    elif words:
        base = words[0][:3].upper()
    else:
        base = "CAT"
    if not base:
        base = "CAT"

    existing_short_terms = {c[0] for c in db.query(models.Category.short_term).all() if c[0]}
    candidate = base
    suffix = 2
    while candidate in existing_short_terms:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate
