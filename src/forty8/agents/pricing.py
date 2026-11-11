"""Step 11 - Pricing. Sends the loss metrics (AAL, OEP at 100 and 250 years) to the pricing tool.
Exception path: a rejection goes to review; the analyst answers 'retry' to send again."""

from __future__ import annotations

from forty8.agents.base import AgentContext
from forty8.agents.util import finish
from forty8.models.common import AgentResult, AuditEntry, ExceptionItem
from forty8.models.pipeline import ModelOutput, PolicyMapping, PricingOutput
from forty8.models.results import PricingSubmission


class PricingAgent:
    key = "pricing"
    output_model = PricingOutput

    def run(self, ctx: AgentContext) -> AgentResult:
        model: ModelOutput = ctx.input("model_track")
        account = ctx.input("policy_mapping").exposure.account
        submission = PricingSubmission(
            account_number=account.AccNumber, insured=account.AccName, currency=model.results.currency,
            aal=model.results.aal, oep_100=model.results.oep_100, oep_250=model.results.oep_250,
            total_tiv=model.total_tiv,
        )
        response = ctx.connectors.pricing_tool.submit(submission)
        out = PricingOutput(submission=submission, response=response)
        audit = [AuditEntry(
            input_ref=f"job:{model.job_id}", confidence=1.0, rule_or_model="pricing_tool",
            decision=(f"accepted by pricing tool, reference {response.reference}" if response.accepted
                      else f"rejected by pricing tool: {response.reason}"),
        )]
        exceptions = [] if response.accepted else [ExceptionItem(
            code="PRICING_REJECTED", subject="pricing_submission", priority=submission.total_tiv,
            reason=f"The pricing tool rejected the loss metrics: {response.reason}. Correct with 'retry' to send again.",
            payload={"submission": submission.model_dump(), "reason": response.reason},
        )]
        return finish(out, audit, exceptions)
