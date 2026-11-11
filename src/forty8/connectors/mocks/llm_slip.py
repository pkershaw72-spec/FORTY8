"""Mock LLM task: slip reading. Stands in for Claude extracting contract terms from slip text.
Deterministic pattern matching; confidence drops when the wording is hedged ("TBA", "approx.",
"as expiring", "to be confirmed"...), when several amounts compete, or a date is ambiguous."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from forty8.connectors.mocks.llm import register_task
from forty8.reference.gazetteer import fold

_LABELLED = re.compile(r"^[^:]{2,60}:")
_HEDGE = re.compile(r"\btba\b|\btbc\b|to be (confirmed|agreed|advised)|approx|as expiring|or such|"
                    r"as may be agreed|where available|subject to")
_MONEY = re.compile(r"(?:USD|EUR|GBP|JPY|MXN|US\$|\$)\s*([\d,]+(?:\.\d+)?)\s*(k|m|mn|million|bn|billion)?\b", re.I)
_SCALE = {"k": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_LONG_DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})", re.I)
_NUM_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
PERIL_WORDS = {
    "windstorm": r"wind|hurricane|typhoon|cyclone|huracan",
    "earthquake": r"earthquake|quake|seismic|terremoto|sismo",
    "flood": r"flood|inundacion",
}


def merge_wrapped(text: str) -> list[str]:
    """Re-join lines that a PDF wrapped: a line without a leading 'Label:' continues the previous one."""
    merged: list[str] = []
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        if merged and not _LABELLED.match(line):
            merged[-1] += " " + line
        else:
            merged.append(line)
    return merged


def amounts(text: str) -> list[float]:
    return [float(n.replace(",", "")) * _SCALE.get((s or "").lower(), 1.0) for n, s in _MONEY.findall(text)]


def perils_in(text: str) -> list[str]:
    folded = fold(text)
    return [peril for peril, pattern in PERIL_WORDS.items() if re.search(pattern, folded)]


def _term(name: str, value: Any, confidence: float, line: str) -> dict[str, Any]:
    if _HEDGE.search(fold(line)):
        confidence = min(confidence, 0.5)
    return {"name": name, "value": value, "confidence": round(confidence, 2), "source_text": line}


def _period(rest: str, line: str) -> list[dict[str, Any]]:
    long_dates = [date(int(y), _MONTHS.index(m.lower()) + 1, int(d)) for d, m, y in _LONG_DATE.findall(rest)]
    if len(long_dates) >= 2:
        return [_term("inception", long_dates[0].isoformat(), 0.97, line),
                _term("expiry", long_dates[1].isoformat(), 0.97, line)]
    if match := _NUM_DATE.search(rest):
        a, b, year = (int(g) for g in match.groups())
        year += 2000 if year < 100 else 0
        confidence = 0.5 if a <= 12 and b <= 12 else 0.85  # 01/02/27: 1 Feb or 2 Jan?
        try:
            start = date(year, b, a)
        except ValueError:
            return []
        end = date(year + 1, b, a) - timedelta(days=1)
        return [_term("inception", start.isoformat(), confidence, line),
                _term("expiry", end.isoformat(), confidence, line)]
    return []


@register_task("slip_terms")
def read_slip_terms(instructions: str, content: str) -> dict[str, Any]:
    terms: list[dict[str, Any]] = []
    in_sublimits = False
    for line in merge_wrapped(content):
        label, _, rest = line.partition(":")
        key, rest = fold(label), rest.strip()
        money = amounts(rest)
        if key in ("unique market reference", "umr"):
            terms.append(_term("umr", rest, 0.98, line))
        elif key in ("insured", "assured", "asegurado", "name of insured"):
            terms.append(_term("insured", rest, 0.97, line))
        elif key in ("period", "period of insurance", "vigencia"):
            terms += _period(rest, line)
        elif key in ("limit of liability", "limit", "limite"):
            terms.append(_term("limit", money[0] if money else None, 0.96 if len(money) == 1 else 0.45, line))
        elif key in ("deductible", "deducible", "retention"):
            terms.append(_term("deductible", money[0] if money else None, 0.95 if len(money) == 1 else 0.1, line))
        elif key in ("perils", "perils insured", "riesgos"):
            terms.append(_term("perils", perils_in(rest), 0.95, line))
        elif key in ("currency", "moneda"):
            terms.append(_term("currency", rest[:3].upper(), 0.98, line))
        elif key == "total insured value":
            terms.append(_term("total_tiv", money[0] if money else None, 0.95 if money else 0.2, line))
        elif key == "sublimits":
            in_sublimits = True
        elif in_sublimits and " - " in label and perils_in(label.split(" - ")[0]):
            peril = perils_in(label.split(" - ")[0])[0]
            zone_text = label.split(" - ", 1)[1].strip()
            zone_key = re.sub(r"[^a-z0-9]+", "_", fold(zone_text)).strip("_")
            basis = "aggregate" if "aggregate" in fold(rest) else "occurrence"
            terms.append(_term(
                f"sublimit:{peril}:{zone_key}",
                {"peril": peril, "zone_text": zone_text, "limit": money[0] if money else None, "basis": basis},
                0.93 if len(money) == 1 else 0.3, line,
            ))
    return {"terms": terms}
