"""Step 1 - Intake. Reads the SoV (xlsx/csv, any number of tabs) and the slip (docx/pdf),
detects language, and picks up the insured name. No template is required.
Exception path: a file that cannot be read goes to the review queue; the analyst supplies
a replacement path."""

from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.util import finish, is_empty
from forty8.lang import detect_language
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem
from forty8.models.pipeline import IntakeOutput, RawSheet

SOV_FORMATS = {".xlsx", ".xlsm", ".csv"}
SLIP_FORMATS = {".docx", ".pdf"}
_INSURED = re.compile(r"^\s*(?:insured|assured|asegurado|name of insured)\s*:\s*(.+?)\s*$", re.I | re.M)
_NUMERIC = re.compile(r"^-?(?:[1-9]\d*|0)(?:\.\d+)?$")


class UnreadableFile(Exception):
    pass


def _cell(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value.strip() or None
    return value


def _csv_cell(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    if _NUMERIC.match(text):  # leading-zero strings such as postcodes stay as text
        return float(text) if "." in text else int(text)
    return text


def _trim(row: list[Any]) -> list[Any]:
    while row and row[-1] is None:
        row.pop()
    return row


def read_sov(path: Path) -> list[RawSheet]:
    suffix = path.suffix.lower()
    if suffix not in SOV_FORMATS:
        raise UnreadableFile(f"unsupported SoV format '{suffix}' (expected xlsx or csv)")
    if not path.exists():
        raise UnreadableFile("file not found")
    try:
        if suffix == ".csv":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                text = path.read_text(encoding="latin-1")
            rows = [_trim([_csv_cell(c) for c in row]) for row in csv.reader(text.splitlines())]
            sheets = [RawSheet(name=path.stem, rows=rows)]
        else:
            from openpyxl import load_workbook

            wb = load_workbook(path, data_only=True, read_only=True)
            sheets = [
                RawSheet(name=ws.title, rows=[_trim([_cell(c) for c in row]) for row in ws.iter_rows(values_only=True)])
                for ws in wb.worksheets
            ]
    except UnreadableFile:
        raise
    except Exception as exc:
        raise UnreadableFile(f"could not be parsed as {suffix[1:]} ({type(exc).__name__})") from exc
    if not any(any(not is_empty(c) for c in row) for sheet in sheets for row in sheet.rows):
        raise UnreadableFile("file contains no data")
    return sheets


def read_slip(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SLIP_FORMATS:
        raise UnreadableFile(f"unsupported slip format '{suffix}' (expected docx or pdf)")
    if not path.exists():
        raise UnreadableFile("file not found")
    try:
        if suffix == ".docx":
            from docx import Document

            doc = Document(str(path))
            lines = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                lines += [": ".join(c.text.strip() for c in row.cells if c.text.strip()) for row in table.rows]
        else:
            from pypdf import PdfReader

            lines = [page.extract_text() or "" for page in PdfReader(str(path)).pages]
    except Exception as exc:
        raise UnreadableFile(f"could not be parsed as {suffix[1:]} ({type(exc).__name__})") from exc
    text = "\n".join(line for line in lines if line.strip())
    if len(text.strip()) < 20:
        raise UnreadableFile("no readable text (scanned image?)")
    return text


def _sheet_text(sheet: RawSheet) -> str:
    return " ".join(str(c) for row in sheet.rows[:15] for c in row if isinstance(c, str))


class IntakeAgent:
    key = "intake"
    output_model = IntakeOutput

    def run(self, ctx: AgentContext) -> AgentResult:
        audit: list[AuditEntry] = []
        exceptions: list[ExceptionItem] = []
        paths = {"sov": ctx.submission.sov_path, "slip": ctx.submission.slip_path}
        for kind in paths:  # an analyst may have supplied a replacement file
            if fix := ctx.correction_for(f"file:{kind}"):
                paths[kind] = str(fix.value)
                audit.append(AuditEntry(input_ref=f"file:{kind}", decision=f"using replacement file {fix.value}",
                                        confidence=1.0, rule_or_model=f"analyst:{fix.analyst}"))
        out = IntakeOutput(sov_path=paths["sov"], slip_path=paths["slip"], insured=ctx.submission.insured)

        for kind, reader in (("sov", read_sov), ("slip", read_slip)):
            path = Path(paths[kind])
            try:
                parsed = reader(path)
            except UnreadableFile as exc:
                exceptions.append(ExceptionItem(
                    code="UNREADABLE_FILE", subject=f"file:{kind}",
                    reason=f"{kind.upper() if kind == 'sov' else 'Slip'} file '{path.name}' is unreadable: {exc}. "
                           "Correct with the path to a readable copy.",
                    payload={"path": str(path)},
                ))
                audit.append(AuditEntry(input_ref=str(path), decision=f"unreadable: {exc}", confidence=1.0,
                                        rule_or_model="rule:file_parse"))
                continue
            if kind == "sov":
                out.sheets = parsed
                for sheet in parsed:
                    lang, conf = detect_language(_sheet_text(sheet))
                    out.sheet_languages[sheet.name] = lang
                    audit.append(AuditEntry(input_ref=f"{path.name}#{sheet.name}", confidence=conf,
                                            decision=f"tab read ({len(sheet.rows)} rows), language {lang}",
                                            rule_or_model="rule:language_wordlist"))
            else:
                out.slip_text = parsed
                out.slip_language, conf = detect_language(parsed)
                audit.append(AuditEntry(input_ref=path.name, confidence=conf,
                                        decision=f"slip read ({len(parsed)} chars), language {out.slip_language}",
                                        rule_or_model="rule:language_wordlist"))
                if out.insured is None and (match := _INSURED.search(parsed)):
                    out.insured = match.group(1)
                    audit.append(AuditEntry(input_ref=path.name, decision=f"insured identified as '{out.insured}'",
                                            confidence=0.9, rule_or_model="rule:insured_label"))
        langs = [*out.sheet_languages.values(), out.slip_language]
        out.languages = sorted({lang for lang in langs if lang != "unknown"})
        return finish(out, audit, exceptions)
