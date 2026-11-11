"""Step 3 - Schema mapping. Maps source headers (any language) to the target schema.
Order: approved learned mapping, then the schema's synonym table, then the LLM.
Exception path: a tab with a required column still unmapped goes to review."""

from __future__ import annotations

import json
from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.util import finish, is_number
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.pipeline import CleanTable, ColumnMapping, MappedRow, MappedTable
from forty8.schema.target import TargetSchema, header_unit, load_schema, normalise_header

SQFT_TO_M2 = 0.09290304
IGNORE = "ignore"
LLM_INSTRUCTIONS = (
    "Map each source spreadsheet header to one of the target fields, or null if none fits. "
    "Headers may be in any language. Return a confidence between 0 and 1 for each mapping."
)


class SchemaMappingAgent:
    key = "schema_mapping"
    output_model = MappedTable

    def run(self, ctx: AgentContext) -> AgentResult:
        clean: CleanTable = ctx.input("cleaning")
        schema = load_schema(ctx.settings.schema_.target)
        synonyms = schema.synonym_index()
        audit: list[AuditEntry] = []
        exceptions: list[ExceptionItem] = []
        out = MappedTable(target_schema=schema.format)

        tabs = [t for t in clean.tabs if not t.skipped]
        decided: dict[str, tuple[str | None, float, str]] = {}  # header -> (field, confidence, method)
        for header in dict.fromkeys(h for t in tabs for h in t.headers):
            if fix := ctx.correction_for(f"header:{header}"):
                target = None if str(fix.value).strip().lower() == IGNORE else schema.resolve(str(fix.value))
                if target or str(fix.value).strip().lower() == IGNORE:
                    decided[header] = (target, 1.0, f"analyst:{fix.analyst}")
                    continue
            learned = ctx.learned.lookup("header", header)
            if learned is not None and (learned.value == IGNORE or schema.resolve(str(learned.value))):
                target = None if learned.value == IGNORE else schema.resolve(str(learned.value))
                decided[header] = (target, 1.0, f"learned_mapping:{learned.id}")
            elif (key := normalise_header(header)) in synonyms:
                decided[header] = (synonyms[key], 1.0, "rule:header_synonyms")

        unknown = [h for t in tabs for h in t.headers if h not in decided]
        if unknown:
            decided.update(self._ask_llm(ctx, list(dict.fromkeys(unknown)), clean, schema))

        for tab in tabs:
            taken: dict[str, ColumnMapping] = {}
            for header in tab.headers:
                target, confidence, method = decided.get(header, (None, 0.0, "unmapped"))
                mapping = ColumnMapping(tab=tab.tab, source_header=header, target_field=target,
                                        confidence=confidence, method=method,
                                        unit=header_unit(header) if target == "FloorArea" else None)
                if target and target in taken:  # two headers claim one field: keep the more confident
                    loser = mapping if taken[target].confidence >= confidence else taken[target]
                    loser.target_field, loser.method = None, f"{loser.method} (duplicate of {target}, ignored)"
                    if loser is not mapping:
                        taken[target] = mapping
                elif target:
                    taken[target] = mapping
                out.mappings.append(mapping)
            for mapping in (m for m in out.mappings if m.tab == tab.tab):
                audit.append(AuditEntry(
                    input_ref=f"header:{tab.tab}:{mapping.source_header}", confidence=mapping.confidence,
                    rule_or_model=mapping.method,
                    decision=f"mapped to {mapping.target_field}" if mapping.target_field else "not mapped (ignored)",
                ))
            missing = [f for f in schema.required if f not in taken]
            if missing:
                exceptions += self._raise_missing(ctx, clean, tab.tab, missing, out)

        by_tab = {(m.tab, m.source_header): m for m in out.mappings if m.target_field}
        for row in clean.rows:
            values: dict[str, Any] = {}
            for header, value in row.values.items():
                mapping = by_tab.get((row.lineage.tab, header))
                if mapping is None:
                    continue
                if mapping.unit == "sqft" and is_number(value):
                    value = round(value * SQFT_TO_M2, 1)
                values[mapping.target_field] = value
            out.rows.append(MappedRow(lineage=row.lineage, values=values))
        return finish(out, audit, exceptions)

    def _ask_llm(self, ctx: AgentContext, headers: list[str], clean: CleanTable,
                 schema: TargetSchema) -> dict[str, tuple[str | None, float, str]]:
        llm = ctx.connectors.llm
        content = json.dumps({
            "headers": [{"header": h, "samples": _samples(clean, h)} for h in headers],
            "fields": [{"field": f.canonical, "synonyms": f.synonyms} for f in schema.fields],
        }, ensure_ascii=False, default=str)
        response = llm.complete("header_mapping", LLM_INSTRUCTIONS, content)
        result: dict[str, tuple[str | None, float, str]] = {}
        threshold = ctx.settings.thresholds.header_min_confidence
        for item in response.data.get("mappings", []):
            target = schema.resolve(item["field"]) if item.get("field") else None
            confidence = float(item.get("confidence") or 0.0)
            if item.get("header") in headers and target and confidence >= threshold:
                result[item["header"]] = (target, confidence, f"llm:{llm.model}")
            elif item.get("header") in headers:
                result[item["header"]] = (None, confidence, f"llm:{llm.model} (below {threshold:.0%}, not mapped)")
        return result

    def _raise_missing(self, ctx: AgentContext, clean: CleanTable, tab: str, missing: list[str],
                       out: MappedTable) -> list[ExceptionItem]:
        """One review item per unmapped header in a tab that lacks a required column."""
        unmapped = [m for m in out.mappings if m.tab == tab and m.target_field is None
                    and not m.method.startswith(("analyst", "learned"))]
        affected = float(sum(1 for r in clean.rows if r.lineage.tab == tab))  # rows held up
        if not unmapped:
            return [ExceptionItem(
                code="REQUIRED_COLUMN_ABSENT", subject=f"required:{tab}",
                reason=f"Tab '{tab}' has no column for required field(s) {', '.join(missing)} and no unmapped "
                       "headers left to assign. The source file needs fixing.",
                payload={"tab": tab, "missing": missing}, priority=affected,
            )]
        return [ExceptionItem(
            code="UNMAPPED_REQUIRED_COLUMN", subject=f"header:{m.source_header}",
            reason=f"Tab '{tab}' has no column for required field(s) {', '.join(missing)}. Header "
                   f"'{m.source_header}' is unmapped. Correct with the target field name, or 'ignore'.",
            payload={"tab": tab, "header": m.source_header, "missing_required": missing,
                     "samples": _samples(clean, m.source_header), "target_fields": load_schema(out.target_schema).canonical_names},
            priority=affected, learn=LearnSpec(kind="header", key=m.source_header),
        ) for m in unmapped]


def _samples(clean: CleanTable, header: str, n: int = 3) -> list[Any]:
    found = []
    for row in clean.rows:
        if header in row.values:
            found.append(row.values[header])
            if len(found) == n:
                break
    return found
