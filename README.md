# Forty8

Agentic pipeline that takes a broker nat-cat property submission (Schedule of Values + slip),
produces a model-ready OED exposure set, runs a cat model and sends loss metrics to pricing.
See [SPEC.md](SPEC.md). Internal proof of concept: **synthetic data and mock connectors only**.

## Status
| Phase | Scope | State |
|---|---|---|
| 1 | Skeleton: orchestrator, audit log, review queue, learning loop, connector interfaces + mocks, synthetic data | done |
| 2 | Steps 1-5 and the slip lane | next |
| 3 | Steps 6-7: geocoding cascade, enrichment | |
| 4 | Steps 8-11, end-to-end tests, metrics report | |
| 5 | Web page | |

Until a step's agent is built it is a placeholder that passes straight through.

## Quick start
```bash
pip install -e ".[dev]"
pytest
forty8 synth --profile messy --out synthetic
forty8 run synthetic/sov_messy.xlsx synthetic/slip_messy.docx --broker "Northlight Broking (fictional)"
forty8 status                      # list runs
forty8 review list                 # open exceptions
forty8 review correct <id> --value <correction> --analyst <name>
forty8 review resume <run>
forty8 learn list                  # corrections waiting for a second analyst
forty8 learn approve <id> --analyst <other name>
forty8 audit <run>
forty8 metrics <run>
```

## How it fits together
- `config/forty8.toml` holds every threshold (80% TIV at street level, 85% slip confidence, ML confidence)
  and selects each connector implementation. Secrets come from environment variables named there.
- `src/forty8/orchestrator` is an explicit state machine: 11 main-lane steps, with the slip lane (2b)
  running in parallel after intake and joined at step 9. A step that raises exceptions parks in
  `AWAITING_REVIEW`; `resume` re-runs it with the analyst's corrections.
- `src/forty8/agents` holds one small agent per step. Agents are pure: they return an `AgentResult`
  (output, exceptions, audit entries) and the orchestrator does all persistence.
- `src/forty8/store` is SQLite: runs, step state, step outputs, the append-only audit log,
  the review queue and learned mappings.
- Learning loop: a correction fixes the current run and is stored as a *proposed* mapping. It is reused
  on later submissions only after a **different** analyst approves it; every reuse is audit-logged.
- `src/forty8/connectors` has an interface per external system, with mocks. H3 uses the real library.
- `src/forty8/synth` generates clean and messy submissions; each defect is an independent switch.
