"""Step 4 - Coding. Builds typed locations and maps free-text occupancy and construction to
model codes using approved learned mappings and the rule/lookup tables.
No exception path: values with no rule match are handed to step 5."""

from __future__ import annotations

from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.util import finish, is_empty, to_float, to_int
from forty8.models.common import AgentResult, AuditEntry
from forty8.models.exposure import Location, SourcedValue
from forty8.models.pipeline import CodedLocations, MappedRow, MappedTable, PendingCode
from forty8.reference.coding_rules import CODES, resolve_class, rule_lookup
from forty8.reference.gazetteer import country_code

FIELDS = {"occupancy": ("occupancy_text", "OccupancyCode"), "construction": ("construction_text", "ConstructionCode")}


def _text(value: Any) -> str | None:
    if is_empty(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def build_location(row: MappedRow, index: int) -> Location:
    v = row.values
    return Location(
        LocNumber=_text(v.get("LocNumber")) or f"row-{index + 1}",
        LocName=_text(v.get("LocName")), StreetAddress=_text(v.get("StreetAddress")), City=_text(v.get("City")),
        AreaCode=_text(v.get("AreaCode")), PostalCode=_text(v.get("PostalCode")),
        CountryCode=country_code(_text(v.get("CountryCode")) or "") or None,
        YearBuilt=to_int(v.get("YearBuilt")), NumberOfStoreys=to_int(v.get("NumberOfStoreys")),
        FloorArea=to_float(v.get("FloorArea")),
        BuildingTIV=to_float(v.get("BuildingTIV")) or 0.0, ContentsTIV=to_float(v.get("ContentsTIV")) or 0.0,
        BITIV=to_float(v.get("BITIV")) or 0.0, stated_tiv=to_float(v.get("stated_tiv")),
        LocCurrency=(_text(v.get("LocCurrency")) or "").upper() or None,
        occupancy_text=_text(v.get("occupancy_text")), construction_text=_text(v.get("construction_text")),
        lineage=row.lineage,
    )


def set_code(loc: Location, field: str, label: str, method: str, confidence: float) -> None:
    code = CODES[field][label]
    setattr(loc, FIELDS[field][1], code)
    loc.sources[FIELDS[field][1]] = SourcedValue(value=code, source=method, confidence=confidence)


class CodingAgent:
    key = "coding"
    output_model = CodedLocations

    def run(self, ctx: AgentContext) -> AgentResult:
        mapped: MappedTable = ctx.input("schema_mapping")
        out = CodedLocations(locations=[build_location(row, i) for i, row in enumerate(mapped.rows)])
        audit: list[AuditEntry] = [AuditEntry(
            input_ref="mapped_table", decision=f"built {len(out.locations)} locations", confidence=1.0,
            rule_or_model="rule:type_coercion",
        )]
        for field, (text_attr, _) in FIELDS.items():
            groups: dict[str, list[int]] = {}
            blanks = 0
            for i, loc in enumerate(out.locations):
                text = getattr(loc, text_attr)
                if text:
                    groups.setdefault(text, []).append(i)
                else:
                    blanks += 1
            for text, indexes in groups.items():
                label, method = None, ""
                learned = ctx.learned.lookup(field, text)
                if learned is not None and (label := resolve_class(field, learned.value)):
                    method = f"learned_mapping:{learned.id}"
                elif label := rule_lookup(field, text):
                    method = f"rule:{field}_lookup"
                if label:
                    for i in indexes:
                        set_code(out.locations[i], field, label, method, 1.0)
                    audit.append(AuditEntry(
                        input_ref=f"{field}:{text}", confidence=1.0, rule_or_model=method,
                        decision=f"coded {CODES[field][label]} ({label}) on {len(indexes)} location(s)",
                    ))
                else:
                    out.pending.append(PendingCode(field=field, text=text, row_indexes=indexes))
                    audit.append(AuditEntry(
                        input_ref=f"{field}:{text}", confidence=0.0, rule_or_model=f"rule:{field}_lookup",
                        decision=f"no rule match; {len(indexes)} location(s) passed to ML classification",
                    ))
            if blanks:
                audit.append(AuditEntry(
                    input_ref=f"{field}:<blank>", confidence=1.0, rule_or_model="rule:blank_value",
                    decision=f"{blanks} location(s) have no {field} text; left for enrichment",
                ))
        return finish(out, audit, [])
