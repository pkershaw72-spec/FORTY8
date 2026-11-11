"""The synthetic generator produces the clean and messy submissions the pipeline is tested on."""

from __future__ import annotations

import json

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

from forty8.connectors.mocks.geocoders import MockBingGeocoder, MockMoodysGeocoder, MockWebSearch
from forty8.models.exposure import AddressQuery
from forty8.synth.generator import (
    HEADERS_DE, HEADERS_EN, HEADERS_ES, UNMAPPED_HEADER, Defects, generate, write_unreadable_file,
)


def _rows(path, tab):
    return [list(r) for r in load_workbook(path, data_only=True)[tab].iter_rows(values_only=True)]


def test_clean_profile_is_tidy(clean_submission):
    wb = load_workbook(clean_submission.sov_path)
    assert wb.sheetnames == ["SoV"]
    rows = _rows(clean_submission.sov_path, "SoV")
    assert rows[0] == HEADERS_EN
    assert len(rows) == 1 + 24 and all(all(c is not None for c in r) for r in rows)
    text = "\n".join(p.text for p in Document(clean_submission.slip_path).paragraphs)
    assert "Limit of Liability: USD" in text and "TBA" not in text and "SYNTHETIC TEST DATA" in text


def test_generation_is_reproducible(tmp_path):
    a = generate(tmp_path / "a", profile="messy", seed=7)
    b = generate(tmp_path / "b", profile="messy", seed=7)
    assert json.loads(open(a.truth_path).read()) == json.loads(open(b.truth_path).read())


def test_messy_profile_contains_every_defect(messy_submission):
    path = messy_submission.sov_path
    assert load_workbook(path).sheetnames == ["USA", "Europe", "Japan", "México", "Notes"]

    usa = _rows(path, "USA")
    assert usa[3][:3] == HEADERS_EN[:3]                       # three title rows above the header
    assert any(str(r[0]).startswith("Subtotal") for r in usa) and usa[-1][0] == "TOTAL"
    assert any(all(c is None for c in r) for r in usa[4:])    # blank rows
    data = [r for r in usa[4:] if isinstance(r[0], int)]
    assert len(data) == len({r[0] for r in data}) + 1         # one duplicated row
    assert usa[-1][15] == sum(r[15] for r in data) + 5_000_000  # stated total does not reconcile
    assert any(r[9] is None for r in data) and any(r[10] is None for r in data)
    assert any(r[9] == 1066 for r in data)
    assert any(r[8] == "Wood Frame" and r[10] == 25 for r in data)

    europe = _rows(path, "Europe")
    assert europe[3][:3] == HEADERS_DE[:3] and europe[4][:3] == HEADERS_EN[:3]  # two candidate header rows
    assert UNMAPPED_HEADER in _rows(path, "Japan")[3]
    assert _rows(path, "México")[3][:3] == HEADERS_ES[:3]

    slip = "\n".join(p.text for p in Document(messy_submission.slip_path).paragraphs)
    assert "TBA" in slip and "as may be agreed" in slip and "Schedule 2" in slip


def test_messy_addresses_break_the_80pct_tiv_check_until_the_top_site_is_fixed(messy_submission):
    """Mirror of the step 6 cascade, to prove the test data exercises the analyst path."""
    moodys, bing, web = MockMoodysGeocoder(), MockBingGeocoder(), MockWebSearch()
    total = sum(loc.tiv_usd for loc in messy_submission.locations)
    unresolved = []
    for loc in messy_submission.locations:
        q = AddressQuery(street=loc.street, city=loc.city, postal_code=loc.postcode, country=loc.country)
        ok = moodys.geocode(q).resolution == "rooftop" or bing.geocode(q).resolution == "street" \
            or bool(web.search(q.one_line()))
        if not ok:
            unresolved.append(loc)
    assert {loc.address_class for loc in unresolved} == {"D"} and len(unresolved) == 2
    street_share = 1 - sum(loc.tiv_usd for loc in unresolved) / total
    assert street_share < 0.80
    top = max(unresolved, key=lambda loc: loc.tiv_usd)
    assert street_share + top.tiv_usd / total >= 0.80


def test_single_defect_switch_and_other_formats(tmp_path):
    sub = generate(tmp_path, profile="clean", defects=Defects(title_rows=True), sov_format="csv", slip_format="pdf")
    lines = open(sub.sov_path, encoding="utf-8").read().splitlines()
    assert lines[0].startswith("Meridian Synthetic") and lines[3].startswith("Loc #")
    assert "Limit of Liability" in PdfReader(sub.slip_path).pages[0].extract_text()


def test_unreadable_file(tmp_path):
    path = write_unreadable_file(tmp_path / "broken.xlsx")
    assert path.stat().st_size == 512
