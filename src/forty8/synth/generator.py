"""Synthetic submission generator. Everything here is invented: fictional insureds and broker,
made-up street addresses in real cities, random values. No client data.

Two profiles:
- clean: one tidy English tab and an unambiguous slip, designed to run straight through.
- messy: every defect switched on, so each exception path in the pipeline can be exercised.
Each defect is an independent switch on `Defects`, so tests can turn on exactly one.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from openpyxl import Workbook

from forty8.connectors.mocks.data import slug
from forty8.reference.codes import CONSTRUCTION_CODES, OCCUPANCY_CODES
from forty8.reference.gazetteer import CITIES, City

BROKER = "Northlight Broking (fictional)"
INSURED = {"clean": "Meridian Synthetic Industries Ltd", "messy": "Borealis Synthetic Holdings SA"}
POLICY_YEAR = 2027
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "MXN": 0.055, "JPY": 0.0066}
SQFT_PER_M2 = 10.7639


@dataclass
class Defects:
    title_rows: bool = False               # title lines above the header
    subtotal_rows: bool = False            # subtotal and TOTAL rows
    blank_rows: bool = False
    multi_tab: bool = False                # USA / Europe / Japan tabs plus a non-data Notes tab
    spanish_tab: bool = False              # Mexico tab with Spanish headers and values
    missing_fields: bool = False           # blank year built / storeys on some rows
    variant_free_text: bool = False        # wording the rule tables do not hold (goes to ML)
    unknown_free_text: bool = False        # wording neither rules nor ML can code
    unmapped_header: bool = False          # a required column with a meaningless header
    ambiguous_header: bool = False         # two candidate header rows
    poor_addresses: bool = False           # estates, highway markers and "address to follow"
    duplicate_row: bool = False
    tiv_mismatch: bool = False             # stated TOTAL does not equal the sum of rows
    implausible_construction: bool = False  # wood frame, 25 storeys
    year_out_of_range: bool = False
    yoy_change: bool = False               # one location's TIV far above the prior year
    ambiguous_slip: bool = False           # hedged limit, deductible "TBA", ambiguous date
    undefined_sublimit_zone: bool = False  # sublimit refers to a zone nobody has defined

    @classmethod
    def all_on(cls) -> "Defects":
        return cls(**{f.name: True for f in fields(cls)})


PROFILES = {"clean": Defects(), "messy": Defects.all_on()}

# class -> (English canonical, English variants, Spanish canonical, Spanish variants)
OCCUPANCY_TEXT = {
    "warehouse": ("Warehouse", ["Distribution Centre", "DC / cross-dock facility"], "Bodega", ["Centro de Distribución"]),
    "office": ("Office", ["Corporate HQ", "Admin building"], "Oficinas", ["Edificio Corporativo"]),
    "retail": ("Retail Store", ["Shopping Centre", "Showroom"], "Tienda", ["Local Comercial"]),
    "light_manufacturing": ("Light Manufacturing", ["Assembly Plant", "Electronics assembly"], "Manufactura Ligera", ["Planta de Ensamble"]),
    "heavy_manufacturing": ("Heavy Manufacturing", ["Steel Fabrication Works", "Foundry"], "Manufactura Pesada", ["Fundición"]),
    "food_processing": ("Food Processing", ["Bottling Plant", "Cold store & packing"], "Procesadora de Alimentos", ["Planta Embotelladora"]),
    "hotel": ("Hotel", ["Resort", "Serviced apartments"], "Hotel", ["Complejo Turístico"]),
    "data_centre": ("Data Centre", ["Server farm", "Colocation facility"], "Centro de Datos", ["Sala de Servidores"]),
}
CONSTRUCTION_TEXT = {
    "wood": ("Wood Frame", ["Timber", "Stick built"], "Madera", ["Estructura de Madera"]),
    "masonry": ("Masonry", ["Brick", "Joisted masonry (JM)"], "Mampostería", ["Tabique"]),
    "reinforced_concrete": ("Reinforced Concrete", ["RC frame", "Cast in-situ concrete"], "Concreto Reforzado", ["Concreto Armado"]),
    "steel": ("Steel Frame", ["Structural steel", "Steel portal frame"], "Estructura de Acero", ["Acero Estructural"]),
    "light_metal": ("Light Metal", ["Pre-engineered metal building", "Metal clad shed"], "Lámina Metálica", ["Nave de Lámina"]),
}
UNKNOWN_OCCUPANCY = "Misc ops - see eng. report"
UNKNOWN_CONSTRUCTION = "Type B"

HEADERS_EN = ["Loc #", "Site Name", "Street Address", "City", "State/Province", "Postal Code", "Country",
              "Occupancy", "Construction", "Year Built", "No. of Stories", "Floor Area (sq ft)",
              "Building Value", "Contents Value", "BI Value (12 months)", "Total Insured Value", "Currency"]
HEADERS_ES = ["No. Ubic.", "Nombre del Sitio", "Dirección", "Ciudad", "Estado", "Código Postal", "País",
              "Giro / Ocupación", "Tipo de Construcción", "Año de Construcción", "Niveles", "Superficie (m2)",
              "Valor Edificio", "Valor Contenidos", "Pérdidas Consecuenciales", "Suma Asegurada Total", "Moneda"]
HEADERS_DE = ["Standort Nr.", "Standortname", "Adresse", "Stadt", "Bundesland", "PLZ", "Land",
              "Nutzung", "Bauart", "Baujahr", "Geschosse", "Fläche (m2)",
              "Gebäudewert", "Inhaltswert", "BU-Wert", "Gesamtversicherungssumme", "Währung"]
VALUE_COLS = (12, 13, 14, 15)  # zero-based indexes of building, contents, BI, TIV
UNMAPPED_HEADER = "Amt (Note 3)"

COUNTRY_NAMES = {"US": "USA", "MX": "México", "GB": "United Kingdom", "DE": "Germany", "NL": "Netherlands",
                 "FR": "France", "IT": "Italy", "ES": "Spain", "JP": "Japan"}
STREET_WORDS = ["Harbor", "Commerce", "Foundry", "Canal", "Meridian", "Granite", "Juniper", "Atlas", "Beacon", "Cedar"]


@dataclass
class TruthLocation:
    loc_number: str
    name: str
    region: str
    tab: str
    city: str
    area: str
    country: str
    postcode: str
    street: str
    address_class: str            # A rooftop | B estate | C highway marker | D unresolvable
    occupancy: str
    construction: str
    occupancy_text: str
    construction_text: str
    occupancy_code: int
    construction_code: int
    year_built: int
    storeys: int
    floor_area_m2: float
    building: float
    contents: float
    bi: float
    currency: str
    defects: list[str] = field(default_factory=list)

    @property
    def tiv(self) -> float:
        return self.building + self.contents + self.bi

    @property
    def tiv_usd(self) -> float:
        return self.tiv * FX_TO_USD[self.currency]


@dataclass
class SlipTruth:
    umr: str
    insured: str
    broker: str
    inception: str
    expiry: str
    currency: str
    limit: float
    deductible: float
    perils: list[str]
    sublimits: list[dict]
    total_tiv_usd: float


@dataclass
class SyntheticSubmission:
    profile: str
    insured: str
    broker: str
    sov_path: str
    slip_path: str
    truth_path: str
    locations: list[TruthLocation]
    slip: SlipTruth
    defects: Defects


# --------------------------------------------------------------------------- locations
def _street(rng: random.Random, city: City, address_class: str) -> tuple[str, str]:
    """Returns (street, postcode). Poor address classes drop the postcode."""
    word, num = rng.choice(STREET_WORDS), rng.randint(10, 9800)
    if address_class == "B":
        estate = f"Nave {rng.randint(2, 9)}, Parque Industrial {word}" if city.region == "MX" \
            else f"Bldg {rng.randint(2, 9)}, {word} Industrial Park"
        return estate, city.postcode
    if address_class == "C":
        return f"Km {rng.randint(5, 40)} Carretera {rng.choice([45, 57, 85])}", ""
    if address_class == "D":
        return ("Varios - ver anexo" if city.region == "MX" else "Address to follow"), ""
    by_country = {
        "US": f"{num} {word} {rng.choice(['St', 'Ave', 'Blvd', 'Dr'])}",
        "MX": f"Av. {word} {num}",
        "GB": f"{num % 300 + 1} {word} Road",
        "DE": f"{word}strasse {num % 300 + 1}",
        "NL": f"{word}weg {num % 300 + 1}",
        "FR": f"{num % 300 + 1} Rue {word}",
        "IT": f"Via {word} {num % 300 + 1}",
        "ES": f"Calle {word} {num % 300 + 1}",
        "JP": f"{rng.randint(1, 5)}-{rng.randint(1, 20)}-{rng.randint(1, 15)} {word}cho",
    }
    return by_country[city.country], city.postcode


def _build_locations(rng: random.Random, n: int, defects: Defects, single_currency: bool) -> list[TruthLocation]:
    quotas = {"US": round(n * 0.42), "EU": round(n * 0.21), "JP": round(n * 0.17)}
    quotas["MX"] = n - sum(quotas.values())
    tabs = {"US": "USA", "EU": "Europe", "JP": "Japan", "MX": "México"}
    locations: list[TruthLocation] = []
    for region in ("US", "EU", "JP", "MX"):
        cities = [c for c in CITIES if c.region == region]
        for i in range(quotas[region]):
            city = cities[i % len(cities)]
            occupancy = rng.choice(list(OCCUPANCY_TEXT))
            construction = rng.choice(["masonry", "reinforced_concrete", "steel", "light_metal"])
            spanish = region == "MX" and defects.spanish_tab
            occ_en, _, occ_es, _ = OCCUPANCY_TEXT[occupancy]
            con_en, _, con_es, _ = CONSTRUCTION_TEXT[construction]
            currency = "USD" if single_currency else city.currency
            usd = rng.randint(4, 60) * 1_000_000
            building = round(usd * 0.55 / FX_TO_USD[currency], -3)
            street, postcode = _street(rng, city, "A")
            locations.append(TruthLocation(
                loc_number=str(len(locations) + 1), name=f"{city.name} {occ_en} {i + 1}", region=region,
                tab=tabs[region] if defects.multi_tab else "SoV",
                city=city.name, area=city.area, country=city.country, postcode=postcode, street=street,
                address_class="A", occupancy=occupancy, construction=construction,
                occupancy_text=occ_es if spanish else occ_en, construction_text=con_es if spanish else con_en,
                occupancy_code=OCCUPANCY_CODES[occupancy], construction_code=CONSTRUCTION_CODES[construction],
                year_built=rng.randint(1965, 2022), storeys=rng.randint(1, 6),
                floor_area_m2=float(rng.randint(8, 400) * 100),
                building=building, contents=round(building * 0.5, -3), bi=round(building * 0.3, -3),
                currency=currency,
            ))
    return locations


def _by_region(locations: list[TruthLocation], region: str) -> list[TruthLocation]:
    return [loc for loc in locations if loc.region == region]


def _apply_location_defects(rng: random.Random, locations: list[TruthLocation], defects: Defects) -> None:
    us, eu, jp, mx = (_by_region(locations, r) for r in ("US", "EU", "JP", "MX"))

    def readdress(loc: TruthLocation, address_class: str) -> None:
        city = next(c for c in CITIES if c.name == loc.city)
        loc.street, loc.postcode = _street(rng, city, address_class)
        loc.address_class = address_class
        loc.defects.append(f"address_{address_class}")

    if defects.poor_addresses:
        readdress(us[0], "B")
        readdress(mx[0], "B")
        readdress(mx[1], "C")
        readdress(mx[2], "C")
        # Two unresolvable sites carrying enough value to break the 80% TIV-at-street-level check.
        for loc, factor in ((us[1], 7), (mx[3], 5)):
            readdress(loc, "D")
            for attr in ("building", "contents", "bi"):
                setattr(loc, attr, getattr(loc, attr) * factor)
    if defects.variant_free_text:
        for loc in (us[2], us[3], eu[0], jp[0], mx[4 % len(mx)]):
            spanish = loc.region == "MX" and defects.spanish_tab
            loc.occupancy_text = rng.choice(OCCUPANCY_TEXT[loc.occupancy][3 if spanish else 1])
            loc.construction_text = rng.choice(CONSTRUCTION_TEXT[loc.construction][3 if spanish else 1])
            loc.defects.append("variant_free_text")
    if defects.unknown_free_text:
        us[4].occupancy_text = UNKNOWN_OCCUPANCY
        us[4].defects.append("unknown_occupancy")
        eu[1].construction_text = UNKNOWN_CONSTRUCTION
        eu[1].defects.append("unknown_construction")
    if defects.missing_fields:
        for loc in (us[5], us[6], eu[2], jp[1]):
            loc.defects.append("missing_year_built")
        for loc in (us[6], jp[2]):
            loc.defects.append("missing_storeys")
    if defects.implausible_construction:
        us[7].construction, us[7].construction_text = "wood", CONSTRUCTION_TEXT["wood"][0]
        us[7].construction_code, us[7].storeys = CONSTRUCTION_CODES["wood"], 25
        us[7].defects.append("implausible_construction")
    if defects.year_out_of_range:
        us[8].year_built = 1066
        us[8].defects.append("year_out_of_range")
    if defects.duplicate_row:
        us[9 % len(us)].defects.append("duplicate_row")
    if defects.yoy_change:
        eu[3 % len(eu)].defects.append("yoy_change")


# --------------------------------------------------------------------------- SoV
def _row(loc: TruthLocation, spanish: bool, area_in_sqft: bool) -> list:
    area = round(loc.floor_area_m2 * SQFT_PER_M2) if area_in_sqft else loc.floor_area_m2
    return [
        int(loc.loc_number), loc.name, loc.street, loc.city, loc.area, loc.postcode, COUNTRY_NAMES[loc.country],
        loc.occupancy_text, loc.construction_text,
        None if "missing_year_built" in loc.defects else loc.year_built,
        None if "missing_storeys" in loc.defects else loc.storeys,
        area, loc.building, loc.contents, loc.bi, loc.tiv, loc.currency,
    ]


def _tab_rows(tab: str, locs: list[TruthLocation], insured: str, defects: Defects) -> list[list]:
    spanish = tab == "México" and defects.spanish_tab
    rows: list[list] = []
    if defects.title_rows:
        rows += [[insured], [f"Schedule of Values - {POLICY_YEAR} renewal - {tab} (SYNTHETIC TEST DATA)"], []]
    headers = list(HEADERS_ES if spanish else HEADERS_EN)
    if tab in ("Europe", "Japan", "México"):
        headers[11] = HEADERS_ES[11] if spanish else "Floor Area (m2)"
    if tab == "Japan" and defects.unmapped_header:
        headers[12] = UNMAPPED_HEADER
    if tab == "Europe" and defects.ambiguous_header:
        rows.append(list(HEADERS_DE))  # bilingual schedule: a German header row above the English one
    rows.append(headers)

    groups: dict[str, list[TruthLocation]] = {}
    for loc in locs:
        groups.setdefault(loc.area if tab == "USA" else loc.country, []).append(loc)
    totals = [0.0] * 4
    for group, members in groups.items():
        sub = [0.0] * 4
        for loc in members:
            row = _row(loc, spanish, area_in_sqft=tab in ("USA", "SoV"))
            for _ in range(2 if "duplicate_row" in loc.defects else 1):
                rows.append(list(row))
                for k, col in enumerate(VALUE_COLS):
                    sub[k] += row[col]
        totals = [t + s for t, s in zip(totals, sub)]
        if defects.subtotal_rows:
            label = f"Subtotal {group}"
            rows.append([label] + [None] * 11 + sub + [None])
            if defects.blank_rows:
                rows.append([])
    if defects.subtotal_rows:
        if tab == "USA" and defects.tiv_mismatch:
            totals[3] += 5_000_000  # stated total overstated: reconciliation must fail
        rows.append(["TOTAL"] + [None] * 11 + totals + [None])
    return rows


def _write_sov(path: Path, locations: list[TruthLocation], insured: str, defects: Defects, sov_format: str) -> None:
    tabs: dict[str, list[TruthLocation]] = {}
    for loc in locations:
        tabs.setdefault(loc.tab, []).append(loc)
    if sov_format == "csv":
        if len(tabs) > 1:
            raise ValueError("csv holds a single table; use xlsx for multi-tab profiles")
        (tab, locs), = tabs.items()
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(_tab_rows(tab, locs, insured, defects))
        return
    wb = Workbook()
    wb.remove(wb.active)
    for tab, locs in tabs.items():
        ws = wb.create_sheet(tab)
        for row in _tab_rows(tab, locs, insured, defects):
            ws.append(row)
    if defects.multi_tab:
        notes = wb.create_sheet("Notes")
        for line in ("Notes to the schedule (SYNTHETIC TEST DATA)",
                     "1. Values are 100% replacement cost.",
                     "2. BI is 12 months gross profit.",
                     "3. Japan building values per local valuation."):
            notes.append([line])
    wb.save(path)


# --------------------------------------------------------------------------- slip
def _money(amount: float, currency: str = "USD") -> str:
    return f"{currency} {amount:,.0f}"


def _slip_truth(profile: str, locations: list[TruthLocation]) -> SlipTruth:
    tiv = sum(loc.tiv_usd for loc in locations)
    limit = max(round(tiv * 0.25, -7), 10_000_000)
    return SlipTruth(
        umr=f"B0000SYN{POLICY_YEAR}{'001' if profile == 'clean' else '002'}",
        insured=INSURED.get(profile, INSURED["messy"]), broker=BROKER,
        inception=f"{POLICY_YEAR}-01-01", expiry=f"{POLICY_YEAR}-12-31", currency="USD",
        limit=limit, deductible=250_000.0, perils=["windstorm", "earthquake", "flood"],
        sublimits=[
            {"peril": "earthquake", "zone": "california_eq", "limit": round(limit * 0.2, -6)},
            {"peril": "earthquake", "zone": "japan_eq", "limit": round(limit * 0.15, -6)},
            {"peril": "windstorm", "zone": "us_tier1_wind", "limit": round(limit * 0.3, -6)},
            {"peril": "flood", "zone": "all", "limit": round(limit * 0.1, -6)},
        ],
        total_tiv_usd=round(tiv, 2),
    )


def slip_lines(truth: SlipTruth, defects: Defects) -> list[str]:
    sub = {(s["peril"], s["zone"]): s["limit"] for s in truth.sublimits}
    if defects.ambiguous_slip:
        period = "Period: 12 months from 01/02/27 (date to be confirmed by the Insured)"
        limit = (f"Limit of Liability: {_money(truth.limit)} any one occurrence, or such lesser amount "
                 f"(approx. {_money(truth.limit * 0.8)}) as may be agreed by the Slip Leader")
        deductible = "Deductible: TBA - expiring was approx. USD 250k each and every occurrence"
        perils = "Perils: As expiring, including Windstorm and Earthquake; Flood where available"
    else:
        period = (f"Period: From 1 January {POLICY_YEAR} to 31 December {POLICY_YEAR}, both days inclusive, "
                  "local standard time at the location of the property insured")
        limit = f"Limit of Liability: {_money(truth.limit)} any one occurrence"
        deductible = f"Deductible: {_money(truth.deductible)} each and every occurrence"
        perils = "Perils: All Risks of Direct Physical Loss or Damage including Windstorm, Earthquake and Flood"
    flood = (f"Flood - High Hazard Flood Zones as per Schedule 2: {_money(sub[('flood', 'all')])} in the annual aggregate"
             if defects.undefined_sublimit_zone
             else f"Flood - all locations: {_money(sub[('flood', 'all')])} in the annual aggregate")
    return [
        "PROPERTY CATASTROPHE SLIP - SYNTHETIC TEST DATA - NOT A REAL CONTRACT",
        f"Unique Market Reference: {truth.umr}",
        "Type: Property Damage and Business Interruption",
        f"Insured: {truth.insured}",
        f"Broker: {truth.broker}",
        period,
        "Interest: Real and personal property and business interruption as per the Schedule of Values",
        limit,
        deductible,
        perils,
        "Sublimits:",
        f"Earthquake - California: {_money(sub[('earthquake', 'california_eq')])} in the annual aggregate",
        f"Earthquake - Japan: {_money(sub[('earthquake', 'japan_eq')])} in the annual aggregate",
        f"Named Windstorm - Tier 1 counties: {_money(sub[('windstorm', 'us_tier1_wind')])} any one occurrence",
        flood,
        f"Total Insured Value: {_money(truth.total_tiv_usd)} as per Schedule of Values on file",
        "Currency: USD",
        "Choice of Law and Jurisdiction: England and Wales",
    ]


def _write_slip(path: Path, lines: list[str], slip_format: str) -> None:
    if slip_format == "docx":
        from docx import Document

        doc = Document()
        doc.add_heading(lines[0], level=1)
        for line in lines[1:]:
            doc.add_paragraph(line)
        doc.save(str(path))
    elif slip_format == "pdf":
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        styles = getSampleStyleSheet()
        story = [Paragraph(lines[0], styles["Heading2"]), Spacer(1, 8)]
        for line in lines[1:]:
            story += [Paragraph(line, styles["BodyText"]), Spacer(1, 4)]
        SimpleDocTemplate(str(path), pagesize=A4).build(story)
    else:
        raise ValueError(f"unsupported slip format '{slip_format}'")


# --------------------------------------------------------------------------- mock stores
def _write_mock_stores(mock_data_dir: Path, insured: str, locations: list[TruthLocation], rng: random.Random) -> None:
    prior = []
    for loc in locations:
        factor = 0.4 if "yoy_change" in loc.defects else 0.97
        prior.append({"loc_number": loc.loc_number, "attributes": {
            "YearBuilt": 1998 if "year_out_of_range" in loc.defects else loc.year_built,
            "NumberOfStoreys": loc.storeys,
            "OccupancyCode": loc.occupancy_code, "ConstructionCode": loc.construction_code,
            "BuildingTIV": round(loc.building * factor, -3), "ContentsTIV": round(loc.contents * factor, -3),
            "BITIV": round(loc.bi * factor, -3), "LocCurrency": loc.currency,
        }})
    # The prior-year file deliberately lacks one of the locations with a blank year built,
    # so that gap has to be filled from third-party building data instead.
    blanks = [loc.loc_number for loc in locations if "missing_year_built" in loc.defects]
    if blanks:
        prior = [p for p in prior if p["loc_number"] != blanks[-1]]
    surveys = [
        {"loc_number": loc.loc_number, "survey_date": f"{POLICY_YEAR - 1}-06-15", "verified": True,
         "attributes": {"ConstructionCode": loc.construction_code, "YearBuilt": loc.year_built,
                        "NumberOfStoreys": loc.storeys, "sprinklered": rng.choice(["yes", "no"])}}
        for loc in locations[::5] if "implausible_construction" not in loc.defects
    ]
    for sub, payload in (
        ("prior_year", {"insured": insured, "year": POLICY_YEAR - 1, "locations": prior}),
        ("surveys", surveys),
    ):
        target = mock_data_dir / sub
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{slug(insured)}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))


# --------------------------------------------------------------------------- public API
def generate(
    out_dir: str | Path,
    profile: str = "clean",
    seed: int = 48,
    n_locations: int = 24,
    defects: Defects | None = None,
    sov_format: str = "xlsx",
    slip_format: str = "docx",
    mock_data_dir: str | Path | None = None,
) -> SyntheticSubmission:
    """Write a synthetic SoV, slip and truth file to out_dir, plus prior-year and survey
    records for the mock stores (in mock_data_dir, default <out_dir>/mock_data)."""
    if defects is None:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile '{profile}' (choose from {sorted(PROFILES)})")
        defects = PROFILES[profile]
    if n_locations < 24:
        raise ValueError("n_locations must be at least 24 so every defect has a location to land on")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    insured = INSURED.get(profile, INSURED["messy"])

    locations = _build_locations(rng, n_locations, defects, single_currency=not defects.multi_tab)
    _apply_location_defects(rng, locations, defects)
    slip = _slip_truth(profile, locations)

    sov_path = out / f"sov_{profile}.{sov_format}"
    slip_path = out / f"slip_{profile}.{slip_format}"
    truth_path = out / f"truth_{profile}.json"
    _write_sov(sov_path, locations, insured, defects, sov_format)
    _write_slip(slip_path, slip_lines(slip, defects), slip_format)
    _write_mock_stores(Path(mock_data_dir) if mock_data_dir else out / "mock_data", insured, locations, rng)
    truth_path.write_text(json.dumps({
        "profile": profile, "seed": seed, "insured": insured, "broker": BROKER,
        "defects": asdict(defects), "slip": asdict(slip),
        "locations": [asdict(loc) | {"tiv": loc.tiv, "tiv_usd": loc.tiv_usd} for loc in locations],
    }, indent=1, ensure_ascii=False))
    return SyntheticSubmission(
        profile=profile, insured=insured, broker=BROKER, sov_path=str(sov_path), slip_path=str(slip_path),
        truth_path=str(truth_path), locations=locations, slip=slip, defects=defects,
    )


def write_unreadable_file(path: str | Path) -> Path:
    """A file with a spreadsheet extension that is not a spreadsheet (intake's exception path)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(random.Random(48).randbytes(512))
    return target
