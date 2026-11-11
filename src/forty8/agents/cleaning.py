"""Step 2 - Cleaning. Removes title rows, subtotal/total rows and blank rows, combines all
tabs into one table and keeps row lineage (tab, original row).
Exception path: a tab with more than one plausible header row (or data but no header) goes
to review; the analyst answers with the header's row number."""

from __future__ import annotations

import math
import re
from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.util import finish, is_empty, is_number
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.exposure import Lineage
from forty8.models.pipeline import CleanRow, CleanTable, IntakeOutput, RawSheet, StatedTotal, TabSummary
from forty8.reference.gazetteer import fold

_SUBTOTAL = re.compile(r"^(sub[\s-]?total|zwischensumme|sous[\s-]?total|小計)")
_TOTAL = re.compile(r"^(grand total|total|suma total|gran total|gesamt|summe|合計)")
MAX_HEADER_SCAN = 60


def _cells(row: list[Any]) -> list[Any]:
    return [c for c in row if not is_empty(c)]


def _is_data(row: list[Any]) -> bool:
    cells = _cells(row)
    return len(cells) >= 3 and any(is_number(c) for c in cells)


def _is_header_like(row: list[Any], width: int) -> bool:
    cells = _cells(row)
    if len(cells) < max(3, math.ceil(0.6 * width)):
        return False
    if not all(isinstance(c, str) and len(c) <= 60 for c in cells):
        return False
    return len({fold(c) for c in cells}) == len(cells)


def signature(row: list[Any]) -> str:
    return "|".join(fold(str(c)) for c in _cells(row))


def total_kind(row: list[Any]) -> str | None:
    cells = _cells(row)
    if not cells or not isinstance(cells[0], str):
        return None
    label = fold(cells[0])
    if _SUBTOTAL.match(label):
        return "subtotal"
    return "total" if _TOTAL.match(label) else None


class CleaningAgent:
    key = "cleaning"
    output_model = CleanTable

    def run(self, ctx: AgentContext) -> AgentResult:
        intake: IntakeOutput = ctx.input("intake")
        out = CleanTable()
        audit: list[AuditEntry] = []
        exceptions: list[ExceptionItem] = []
        for sheet in intake.sheets:
            self._clean_tab(ctx, sheet, out, audit, exceptions)
        audit.append(AuditEntry(
            input_ref="sov", confidence=1.0, rule_or_model="rule:combine_tabs",
            decision=f"combined {sum(1 for t in out.tabs if not t.skipped)} tab(s) into {len(out.rows)} rows",
        ))
        return finish(out, audit, exceptions)

    def _clean_tab(self, ctx: AgentContext, sheet: RawSheet, out: CleanTable,
                   audit: list[AuditEntry], exceptions: list[ExceptionItem]) -> None:
        rows, ref = sheet.rows, f"tab:{sheet.name}"
        summary = TabSummary(tab=sheet.name)
        out.tabs.append(summary)
        first_data = next((i for i, row in enumerate(rows) if _is_data(row)), None)
        if first_data is None:
            summary.skipped, summary.skip_reason = True, "no tabular data"
            audit.append(AuditEntry(input_ref=ref, decision="tab skipped: no tabular data", confidence=0.95,
                                    rule_or_model="rule:non_data_tab"))
            return
        width = max(len(_cells(row)) for row in rows)
        candidates: list[int] = []
        for i in range(min(first_data, MAX_HEADER_SCAN)):
            if _is_header_like(rows[i], width) and signature(rows[i]) not in {signature(rows[c]) for c in candidates}:
                candidates.append(i)

        header_idx, method, confidence = self._choose_header(ctx, sheet, candidates)
        if header_idx is None:
            subject = f"header_row:{sheet.name}"
            options = {str(i + 1): signature(rows[i]) for i in candidates}
            reason = (f"Tab '{sheet.name}' has {len(candidates)} possible header rows "
                      f"(rows {', '.join(options)}). Correct with the row number of the real header."
                      if candidates else
                      f"Tab '{sheet.name}' has data but no recognisable header row. Correct with its row number.")
            exceptions.append(ExceptionItem(
                code="AMBIGUOUS_HEADER_ROW", reason=reason, subject=subject,
                payload={"tab": sheet.name,
                         "candidates": {str(i + 1): _cells(rows[i]) for i in candidates},
                         "first_rows": {str(i + 1): rows[i] for i in range(min(len(rows), first_data + 2))}},
                learn=LearnSpec(kind="header_row", key=_ambiguity_key(rows, candidates), values=options)
                if len(candidates) > 1 else None,
            ))
            summary.skipped, summary.skip_reason = True, "awaiting header row decision"
            return

        headers = [str(c).strip() if not is_empty(c) else "" for c in rows[header_idx]]
        summary.header_row, summary.headers = header_idx + 1, [h for h in headers if h]
        header_sig = signature(rows[header_idx])
        other_headers = {signature(rows[i]) for i in candidates} | {header_sig}
        summary.title_rows_removed = sum(1 for row in rows[:header_idx] if _cells(row))
        for i in range(header_idx + 1, len(rows)):
            row = rows[i]
            if not _cells(row):
                summary.blank_rows_removed += 1
                continue
            if signature(row) in other_headers:  # repeated or alternate-language header
                summary.title_rows_removed += 1
                continue
            values = {h: row[j] for j, h in enumerate(headers) if h and j < len(row) and not is_empty(row[j])}
            if kind := total_kind(row):
                summary.subtotal_rows_removed += 1
                out.stated_totals.append(StatedTotal(
                    tab=sheet.name, kind=kind, label=str(_cells(row)[0]), source_row=i + 1,
                    values={h: v for h, v in values.items() if is_number(v)},
                ))
                continue
            out.rows.append(CleanRow(lineage=Lineage(tab=sheet.name, source_row=i + 1), values=values))
            summary.data_rows += 1
        audit.append(AuditEntry(
            input_ref=ref, confidence=confidence, rule_or_model=method,
            decision=(f"header at row {summary.header_row}; kept {summary.data_rows} data rows; removed "
                      f"{summary.title_rows_removed} title, {summary.subtotal_rows_removed} subtotal/total, "
                      f"{summary.blank_rows_removed} blank"),
            detail=summary.model_dump(),
        ))

    def _choose_header(self, ctx: AgentContext, sheet: RawSheet, candidates: list[int]) -> tuple[int | None, str, float]:
        rows = sheet.rows
        if fix := ctx.correction_for(f"header_row:{sheet.name}"):
            try:
                idx = int(fix.value) - 1
            except (TypeError, ValueError):
                idx = -1
            if 0 <= idx < len(rows) and len(_cells(rows[idx])) >= 2:
                return idx, f"analyst:{fix.analyst}", 1.0
            return None, "", 0.0  # invalid correction: ask again
        if len(candidates) == 1:
            return candidates[0], "rule:header_detection", 0.95
        if len(candidates) > 1:
            learned = ctx.learned.lookup("header_row", _ambiguity_key(rows, candidates))
            if learned is not None:
                for i in candidates:
                    if signature(rows[i]) == learned.value:
                        return i, f"learned_mapping:{learned.id}", 1.0
        return None, "", 0.0


def _ambiguity_key(rows: list[list[Any]], candidates: list[int]) -> str:
    return " || ".join(sorted(signature(rows[i]) for i in candidates))
