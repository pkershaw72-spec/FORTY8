"""Step 5 - ML classification. Classifies the free-text values step 4 could not code.
Order: local classifier; if it is below the confidence threshold, an LLM second opinion.
Exception path: still below threshold goes to review, prioritised by the TIV affected."""

from __future__ import annotations

import json

from forty8.agents.base import AgentContext
from forty8.agents.coding import set_code
from forty8.agents.util import finish
from forty8.ml.classifier import MODEL_NAME, get_classifier
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem, LearnSpec
from forty8.models.pipeline import Classification, ClassifiedLocations, CodedLocations
from forty8.reference.coding_rules import CODES, resolve_class

LLM_INSTRUCTIONS = (
    "Classify the free-text building description into exactly one of the listed classes, or null if it "
    "cannot be determined. The text may be in any language. Return a confidence between 0 and 1."
)


class MLClassifyAgent:
    key = "ml_classify"
    output_model = ClassifiedLocations

    def run(self, ctx: AgentContext) -> AgentResult:
        coded: CodedLocations = ctx.input("coding")
        threshold = ctx.settings.thresholds.ml_min_confidence
        out = ClassifiedLocations(locations=[loc.model_copy(deep=True) for loc in coded.locations])
        audit: list[AuditEntry] = []
        exceptions: list[ExceptionItem] = []

        for pending in coded.pending:
            field, text = pending.field, pending.text
            subject = f"{field}:{text}"
            ranked = get_classifier(field).predict(text)
            label, confidence, method = ranked[0][0], ranked[0][1], f"ml:{MODEL_NAME}"
            invalid_fix = None

            if fix := ctx.correction_for(subject):
                if resolved := resolve_class(field, fix.value):
                    label, confidence, method = resolved, 1.0, f"analyst:{fix.analyst}"
                else:
                    invalid_fix = fix.value
            if confidence < threshold:
                audit.append(AuditEntry(
                    input_ref=subject, confidence=confidence, rule_or_model=f"ml:{MODEL_NAME}",
                    decision=f"classifier suggests {label} below threshold {threshold:.0%}; asking LLM",
                ))
                llm = ctx.connectors.llm
                answer = llm.complete("free_text_coding", LLM_INSTRUCTIONS, json.dumps(
                    {"field": field, "text": text, "classes": [c for c in CODES[field] if c != "unknown"]},
                    ensure_ascii=False)).data
                llm_label = resolve_class(field, answer.get("label")) if answer.get("label") else None
                llm_conf = float(answer.get("confidence") or 0.0)
                if llm_label and llm_conf >= threshold:
                    label, confidence, method = llm_label, llm_conf, f"llm:{llm.model}"
                else:
                    audit.append(AuditEntry(
                        input_ref=subject, confidence=llm_conf, rule_or_model=f"llm:{llm.model}",
                        decision="LLM could not classify with enough confidence; sent to review",
                    ))

            if confidence >= threshold:
                for i in pending.row_indexes:
                    set_code(out.locations[i], field, label, method, round(confidence, 4))
                out.classifications.append(Classification(
                    field=field, text=text, label=label, code=CODES[field][label],
                    confidence=round(confidence, 4), method=method,
                ))
                audit.append(AuditEntry(
                    input_ref=subject, confidence=round(confidence, 4), rule_or_model=method,
                    decision=f"classified {CODES[field][label]} ({label}) on {len(pending.row_indexes)} location(s)",
                ))
                continue

            affected = [out.locations[i] for i in pending.row_indexes]
            reason = (f"Could not classify {field} '{text}' (best guess {ranked[0][0]} at {ranked[0][1]:.0%}, "
                      f"threshold {threshold:.0%}). Correct with a class name or OED code.")
            if invalid_fix is not None:
                reason = f"'{invalid_fix}' is not a valid {field} class or code. " + reason
            exceptions.append(ExceptionItem(
                code="LOW_CONFIDENCE_CLASSIFICATION", subject=subject, reason=reason,
                payload={"field": field, "text": text,
                         "suggestions": [{"label": l, "confidence": round(c, 3)} for l, c in ranked[:3]],
                         "locations": [loc.LocNumber for loc in affected],
                         "valid_classes": {k: v for k, v in CODES[field].items()}},
                priority=sum(loc.tiv for loc in affected),
                learn=LearnSpec(kind=field, key=text),
            ))
        audit.append(AuditEntry(
            input_ref="coding", confidence=1.0, rule_or_model="rule:classification_summary",
            decision=(f"{len(out.classifications)} of {len(coded.pending)} value(s) classified, "
                      f"{len(exceptions)} sent to review" if coded.pending
                      else "nothing to classify: every value was coded by rule in step 4"),
        ))
        return finish(out, audit, exceptions)
