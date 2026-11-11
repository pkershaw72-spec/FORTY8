"""Step 2b - Contract (slip lane). Reads the slip and codes its terms, each with a confidence.
Gate: overall slip confidence (the minimum term confidence) must reach the configured threshold.
Exception path: flagged terms go to the analyst; corrections are applied, stored as learned
mappings (reused once a second analyst approves) and the slip is re-scored."""

from __future__ import annotations

from datetime import date
from typing import Any

from forty8.agents.base import AgentContext
from forty8.agents.util import finish, to_float
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.pipeline import CodedSlip, IntakeOutput, SlipTerm

REQUIRED_TERMS = ["insured", "inception", "expiry", "currency", "limit", "deductible", "perils"]
PERILS = ["windstorm", "earthquake", "flood"]
LLM_INSTRUCTIONS = (
    "Extract the contract terms from this insurance slip: insured, inception and expiry dates (ISO), currency, "
    "limit, deductible, perils covered, total insured value and each sublimit (peril, zone wording, amount). "
    "Give each term a confidence between 0 and 1 and quote the source text. Lower the confidence where the "
    "wording is hedged, conflicting or ambiguous. Do not guess missing terms."
)


def normalise_term(name: str, value: Any, current: Any = None) -> Any:
    """Validate and normalise a term value from an analyst or a learned mapping. None if invalid."""
    if name in ("limit", "deductible", "total_tiv"):
        number = to_float(value)
        return number if number is not None and (number > 0 or (name == "deductible" and number == 0)) else None
    if name in ("inception", "expiry"):
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            return None
    if name == "perils":
        items = value if isinstance(value, list) else str(value).replace(";", ",").split(",")
        perils = [str(p).strip().lower() for p in items if str(p).strip()]
        return perils if perils and all(p in PERILS for p in perils) else None
    if name == "currency":
        code = str(value).strip().upper()
        return code if len(code) == 3 and code.isalpha() else None
    if name.startswith("sublimit:"):
        base = dict(current) if isinstance(current, dict) else {}
        if isinstance(value, dict):
            base.update(value)
        else:
            base["limit"] = to_float(value)
        return base if to_float(base.get("limit")) and base.get("peril") in PERILS else None
    text = str(value).strip() if value is not None else ""
    return text or None


class ContractAgent:
    key = "contract"
    output_model = CodedSlip

    def run(self, ctx: AgentContext) -> AgentResult:
        intake: IntakeOutput = ctx.input("intake")
        threshold = ctx.settings.thresholds.slip_min_confidence
        llm = ctx.connectors.llm
        raw = llm.complete("slip_terms", LLM_INSTRUCTIONS, intake.slip_text).data.get("terms", [])
        terms: dict[str, SlipTerm] = {}
        for item in raw:
            valid = item.get("value") not in (None, "", [])
            terms[item["name"]] = SlipTerm(
                name=item["name"], value=item.get("value"), source_text=item.get("source_text", ""),
                confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))) if valid else 0.0,
                method=f"llm:{llm.model}",
            )
        for name in REQUIRED_TERMS:
            terms.setdefault(name, SlipTerm(name=name, confidence=0.0, method="rule:required_term_not_found"))

        notes: dict[str, str] = {}
        for term in terms.values():
            if term.confidence < threshold and term.source_text:
                learned = ctx.learned.lookup("slip_term", _learn_key(term))
                value = normalise_term(term.name, learned.value, term.value) if learned else None
                if value is not None:
                    term.value, term.confidence, term.method = value, 1.0, f"learned_mapping:{learned.id}"
            if fix := ctx.correction_for(f"term:{term.name}"):
                value = normalise_term(term.name, fix.value, term.value)
                if value is not None:
                    term.value, term.confidence, term.method = value, 1.0, f"analyst:{fix.analyst}"
                else:
                    notes[term.name] = f"The correction '{fix.value}' is not a valid value for {term.name}. "

        start, end = terms["inception"], terms["expiry"]
        if start.value and end.value and str(end.value) <= str(start.value):
            for term in (start, end):
                term.confidence, term.method = 0.0, "rule:expiry_after_inception"
                notes[term.name] = "Expiry is not after inception. "

        out = CodedSlip(terms=list(terms.values()), threshold=threshold)
        out.overall_confidence = min(t.confidence for t in out.terms)
        out.flagged = [t.name for t in out.terms if t.confidence < threshold]

        audit = [AuditEntry(
            input_ref=f"slip:{t.name}", confidence=t.confidence, rule_or_model=t.method,
            decision=f"{t.name} = {t.value!r}" + (" (flagged for review)" if t.name in out.flagged else ""),
            detail={"source_text": t.source_text},
        ) for t in out.terms]
        passed = out.overall_confidence >= threshold
        audit.append(AuditEntry(
            input_ref="slip", confidence=out.overall_confidence, rule_or_model="rule:slip_gate_min_term_confidence",
            decision=(f"overall slip confidence {out.overall_confidence:.0%} "
                      f"{'meets' if passed else 'is below'} threshold {threshold:.0%}"),
        ))
        exceptions = [ExceptionItem(
            code="LOW_CONFIDENCE_SLIP_TERM", subject=f"term:{t.name}",
            reason=(notes.get(t.name, "") + (
                f"Slip term '{t.name}' read as {t.value!r} with confidence {t.confidence:.0%} "
                f"(threshold {threshold:.0%}). Correct with the right value."
                if t.source_text else
                f"Required slip term '{t.name}' was not found in the slip. Correct with its value.")),
            payload={"term": t.name, "extracted_value": t.value, "confidence": t.confidence,
                     "source_text": t.source_text},
            priority=2.0 if t.name in REQUIRED_TERMS else 1.0,
            learn=LearnSpec(kind="slip_term", key=_learn_key(t)) if t.source_text else None,
        ) for t in out.terms if t.name in out.flagged]
        return finish(out, audit, exceptions)


def _learn_key(term: SlipTerm) -> str:
    return f"{term.name}|{term.source_text}"
