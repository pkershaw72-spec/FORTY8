"""Writes a run's model-ready exposure set as OED-style location and account CSV files.

NOTE: column names and peril codes follow OED as best known; verify against the published
specification before loading into a real model."""

from __future__ import annotations

import csv
from pathlib import Path

from forty8.models.exposure import ExposureSet

PERIL_CODES = {"windstorm": "WW1", "earthquake": "QQ1", "flood": "OO1"}
LOCATION_FIELDS = ["LocNumber", "LocName", "StreetAddress", "City", "AreaCode", "PostalCode", "CountryCode",
                   "Latitude", "Longitude", "OccupancyCode", "ConstructionCode", "YearBuilt", "NumberOfStoreys",
                   "FloorArea", "BuildingTIV", "ContentsTIV", "BITIV", "LocCurrency"]


def perils(names: list[str]) -> str:
    return ";".join(PERIL_CODES.get(n, n) for n in names)


def export_oed(exposure: ExposureSet, out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    account = exposure.account
    cond_number = {s.zone: i + 1 for i, s in enumerate(account.sublimits)}

    location_path = out / "location.csv"
    with open(location_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["PortNumber", "AccNumber", *LOCATION_FIELDS, "LocPerilsCovered", "GeocodeResolution",
                         "H3Cell", "CondTag"])
        for loc in exposure.locations:
            writer.writerow([
                "1", account.AccNumber, *[getattr(loc, f) if getattr(loc, f) is not None else "" for f in LOCATION_FIELDS],
                perils(loc.terms.perils if loc.terms else account.PolPerilsCovered),
                loc.geocode.resolution.value if loc.geocode else "", loc.h3_cell or "",
                ";".join(str(cond_number[z]) for z in loc.zones if z in cond_number),
            ])

    account_path = out / "account.csv"
    with open(account_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["PortNumber", "AccNumber", "AccName", "AccCurrency", "PolNumber", "PolInceptionDate",
                         "PolExpiryDate", "PolPerilsCovered", "PolDed6All", "LayerLimit", "CondNumber", "CondName",
                         "CondPeril", "CondLimit6All"])
        base = ["1", account.AccNumber, account.AccName or "", account.AccCurrency or "", account.AccNumber,
                account.PolInceptionDate or "", account.PolExpiryDate or "", perils(account.PolPerilsCovered),
                account.PolDed if account.PolDed is not None else "", account.PolLimit if account.PolLimit is not None else ""]
        if not account.sublimits:
            writer.writerow([*base, "", "", "", ""])
        for sub in account.sublimits:
            writer.writerow([*base, cond_number[sub.zone], f"{sub.peril} - {sub.zone_text or sub.zone}",
                             perils([sub.peril]), sub.limit])
    return location_path, account_path
