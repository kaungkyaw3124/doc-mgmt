"""How a line's unit is shown (loop iteration 30), shared by PDF, XLSX and the
HTML template. "No"/"Nos" (any case, or blank) is the counted unit: "No" for
quantity 1, "Nos" otherwise (0, 2, 2.5). Every other unit prints as stored."""


def display_unit(unit, quantity) -> str:
    u = (unit or "").strip()
    if u.lower() in ("", "no", "nos"):
        return "No" if quantity == 1 else "Nos"
    return u
