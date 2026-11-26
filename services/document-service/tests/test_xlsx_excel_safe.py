"""
Loop iteration 38 — Excel showed "Repaired Records: String properties from
/xl/worksheets/sheet1.xml" and dropped the spacer lines above "Regards,".
Cause: rich-text runs made only of whitespace (" \n", "\n\n\n…"). openpyxl
writes xml:space="preserve" only for runs that also hold visible text, and Excel
rejects/drops a whitespace-only run. LibreOffice keeps it, so only these XML
rules can catch it here (no real Excel in the test environment).
"""
import io
import re
import zipfile

import pytest
from openpyxl import load_workbook
from openpyxl.utils.units import EMU_to_pixels

from app.core.export_quotation import HEADING_PT, LINE_PT, generate_quotation_xlsx
from tests.test_letterhead_header import LETTERHEAD_COMPANY, banner
from tests.test_quotation_visual_style import COMPANY, CUSTOMER, DIRECTOR, _document, _line_item, _png

MD = dict(DIRECTOR, position="Managing Director")
T = re.compile(r"<t(\s[^>/]*)?>(.*?)</t>|<t(\s[^>]*)?/>", re.S)
WS = " \t\n\r "


def _build(n=3, labour=False, terms=None, director=MD, products=None, **kw):
    doc = _document()
    doc.include_labour, doc.terms_and_conditions = labour, terms
    items = []
    for i in range(1, n + 1):
        item = _line_item(i)
        item.remark = "R&D <ok>"
        items.append((item, (products or {}).get(i), []))
    kw.setdefault("company", COMPANY)
    return generate_quotation_xlsx(doc, CUSTOMER, items, director=director, **kw).getvalue()


CASES = {
    "seal": dict(director_seal_bytes=_png((200, 220))),
    "no seal": dict(),
    "no director": dict(director=None),
    "no director name": dict(director=dict(MD, name="")),
    "letterhead + seal": dict(company=LETTERHEAD_COMPANY, logo_bytes=banner(), seal_bytes=_png(), director_seal_bytes=_png()),
    "labour": dict(labour=True, director_seal_bytes=_png()),
    "45 items": dict(n=45, director_seal_bytes=_png()),
    "multi-line terms": dict(terms="Line A\n\n  indented B  \nLine C\n", director_seal_bytes=_png()),
    "& in names": dict(company=dict(COMPANY, name="A & B Co.", support_email="r&d@x.example"),
                       director=dict(MD, name="Tom & Jerry")),
    "unicode": dict(terms="စျေးနှုန်း သက်တမ်း ရက် ၃၀ " * 20, director=dict(MD, name="ဦးဇော်ဇော်"),
                    products={1: {"name": "ကုန်ပစ္စည်း", "description": "အသေးစိတ် " * 40}}),
    "blank descriptions": dict(products={1: {"name": "", "description": "   "}, 2: {"name": "P2", "description": " \n "}}),
}


def _parts(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: z.read(n).decode() for n in z.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml|xl/sharedStrings\.xml", n)}


@pytest.mark.parametrize("case", CASES)
def test_every_text_run_is_excel_safe(case):
    from html import unescape
    for name, xml in _parts(_build(**CASES[case])).items():
        for m in T.finditer(xml):
            attrs, text = (m.group(1) or m.group(3) or ""), unescape(m.group(2) or "")
            where = f"{case}: {name}: {m.group(0)[:80]!r}"
            assert text.strip(WS), f"empty or whitespace-only <t> — {where}"
            if text != text.strip(WS):
                assert 'xml:space="preserve"' in attrs, f"significant whitespace without preserve — {where}"
            assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text), f"control char — {where}"
        for tag in ("oddHeader", "oddFooter", "evenHeader", "evenFooter"):
            for section in re.findall(rf"<{tag}>(.*?)</{tag}>", xml, re.S):
                section = unescape(section)
                for part in re.split(r"&[LCR]", section):
                    assert len(part) <= 255, f"{case}: {tag} section too long"
                # "&" + digits is a font size code; it must not run into digit-leading text
                assert not re.search(r"(?<!&)&\d+\d{3,}", section), f"{case}: ambiguous size code"
                assert section.count("&B") % 2 == 0, f"{case}: unbalanced &B"


# ---- layout: "Regards," right under the last Terms line, seal right under it ----

def _tail(data, col):
    ws = load_workbook(io.BytesIO(data)).active
    (terms,) = [c for r in ws.iter_rows() for c in r if isinstance(c.value, str) and "Terms and Conditions" in c.value]
    return ws, terms, ws.cell(terms.row, col)


@pytest.mark.parametrize("labour", [False, True])
def test_regards_on_the_line_after_the_terms_and_seal_under_it(labour):
    data = _build(labour=labour, director_seal_bytes=_png((200, 220)))
    ws, terms, regards = _tail(data, 9 if labour else 7)
    n_terms = terms.value.count("\n")  # heading line + Terms lines - 1
    lines = regards.value.split("\n")
    assert lines[-1] == "Regards," and all(not s for s in lines[:-1])
    terms_bottom_pt = HEADING_PT + n_terms * LINE_PT
    regards_top_pt = (len(lines) - 1) * LINE_PT
    assert 0 <= regards_top_pt - terms_bottom_pt < LINE_PT, "Regards on the first line below the last Terms line"
    regards_bottom_pt = (len(lines)) * LINE_PT  # all lines are 10pt in the signature cell
    (seal,) = ws._images
    seal_top_pt = EMU_to_pixels(seal.anchor._from.rowOff) * 0.75
    assert abs((seal_top_pt - regards_bottom_pt) * 4 / 3 - 4 * 4 / 3) <= 3, "seal ~4 px under Regards"
    seal_bottom_pt = seal_top_pt + EMU_to_pixels(seal.anchor.ext.height) * 0.75
    row_pt = ws.row_dimensions[terms.row].height
    terms_pt = HEADING_PT + n_terms * LINE_PT + 4
    assert row_pt <= max(terms_pt, seal_bottom_pt + 6) + 0.5, "no dead space below the signature"  # 0.5: px rounding


def test_without_seal_regards_then_name_and_position():
    ws, terms, regards = _tail(_build(), 7)
    lines = regards.value.split("\n")
    assert lines[-3:] == ["Regards,", MD["name"], "Managing Director"]
    assert 0 <= (len(lines) - 3) * LINE_PT - (HEADING_PT + terms.value.count("\n") * LINE_PT) < LINE_PT
