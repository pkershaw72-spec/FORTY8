"""Forty8 command line: run submissions, work the review queue, view audit log and metrics."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from forty8.config import load_settings
from forty8.models.common import MappingStatus
from forty8.orchestrator.engine import Orchestrator
from forty8.orchestrator.metrics import run_metrics
from forty8.orchestrator.pipeline import STEPS


def _orchestrator(args: argparse.Namespace) -> Orchestrator:
    return Orchestrator(load_settings(args.config))


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [headers] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _print_status(orc: Orchestrator, run_id: str) -> None:
    run, steps = orc.store.get_run(run_id), orc.store.get_steps(run_id)
    print(f"Run {run_id}: {run['status']}   broker: {run['broker']}")
    print(_table(
        ["step", "agent", "lane", "status", "attempts", "seconds"],
        [[s.number, s.name, s.lane, steps[s.key]["status"], steps[s.key]["attempts"],
          f"{steps[s.key]['elapsed_seconds']:.3f}"] for s in STEPS],
    ))
    open_items = orc.review.list(run_id)
    if open_items:
        print(f"\n{len(open_items)} open review item(s): forty8 review list --run {run_id}")


def cmd_synth(args: argparse.Namespace) -> int:
    from forty8.synth.generator import generate

    settings = load_settings(args.config)
    sub = generate(
        args.out, profile=args.profile, seed=args.seed, sov_format=args.sov_format,
        slip_format=args.slip_format, mock_data_dir=settings.storage.mock_data_dir,
    )
    print(f"Synthetic '{sub.profile}' submission for {sub.insured} ({len(sub.locations)} locations)")
    print(f"  SoV:   {sub.sov_path}\n  Slip:  {sub.slip_path}\n  Truth: {sub.truth_path}")
    print(f'Run it: forty8 run {sub.sov_path} {sub.slip_path} --broker "{sub.broker}"')
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    orc = _orchestrator(args)
    run_id = orc.start(args.sov, args.slip, broker=args.broker, insured=args.insured)
    _print_status(orc, run_id)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    orc = _orchestrator(args)
    if args.run:
        _print_status(orc, args.run)
    else:
        print(_table(["run", "status", "broker", "created"],
                     [[r["run_id"], r["status"], r["broker"], r["created_at"][:19]] for r in orc.store.list_runs()]))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    orc = _orchestrator(args)
    if args.action == "list":
        items = orc.review.list(args.run, open_only=not args.all)
        print(_table(["id", "run", "step", "status", "priority", "code", "reason"],
                     [[i.id, i.run_id, i.step, i.status.value, f"{i.priority:,.0f}", i.code, i.reason] for i in items]))
    elif args.action == "show":
        print(orc.review.get(args.id).model_dump_json(indent=2))
    elif args.action == "correct":
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value  # plain strings do not need JSON quoting
        item, mapping = orc.review.correct(args.id, value, args.analyst)
        print(f"Review item {item.id} resolved by {args.analyst}.")
        if mapping:
            key = mapping.key if len(mapping.key) <= 60 else mapping.key[:57] + "..."
            print(f"Learned mapping {mapping.id} proposed ({mapping.kind}: {key!r}); "
                  f"a second analyst must approve it: forty8 learn approve {mapping.id} --analyst <name>")
        print(f"Resume when ready: forty8 review resume {item.run_id}")
    elif args.action == "resume":
        orc.resume(args.run_id)
        _print_status(orc, args.run_id)
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    orc = _orchestrator(args)
    if args.action == "list":
        status = MappingStatus(args.status) if args.status else None
        print(_table(["id", "kind", "scope", "key", "value", "status", "proposed by", "approved by"],
                     [[m.id, m.kind, m.scope, m.key[:60], json.dumps(m.value)[:60], m.status.value, m.proposed_by,
                       m.approved_by or ""] for m in orc.learning.list(status)]))
    elif args.action == "approve":
        mapping = orc.learning.approve(args.id, args.analyst, scope=args.scope)
        print(f"Mapping {mapping.id} approved by {args.analyst} (scope: {mapping.scope}).")
    elif args.action == "revoke":
        orc.learning.revoke(args.id, args.analyst)
        print(f"Mapping {args.id} revoked.")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    events = _orchestrator(args).audit.for_run(args.run)
    print(_table(["id", "time (UTC)", "agent", "input", "decision", "conf", "rule / model"],
                 [[e.id, e.ts.strftime("%H:%M:%S.%f")[:-3], e.agent, e.input_ref, e.decision,
                   f"{e.confidence:.2f}", e.rule_or_model] for e in events]))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from forty8.orchestrator.report import render_report

    text = render_report(_orchestrator(args), args.run)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Report written to {args.out}")
    else:
        print(text)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from forty8.export import export_oed

    mapping = _orchestrator(args).output(args.run, "policy_mapping")
    if mapping is None:
        raise ValueError(f"run {args.run} has not reached policy mapping (step 9) yet")
    location, account = export_oed(mapping.exposure, args.out)
    print(f"Exposure set written:\n  {location}\n  {account}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from forty8.web.app import create_app

    print(f"Forty8 web page: http://{args.host}:{args.port}")
    uvicorn.run(create_app(load_settings(args.config)), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    orc = _orchestrator(args)
    if not args.run:
        from forty8.orchestrator.report import summary_across_runs

        for key, value in summary_across_runs(orc).items():
            print(f"{key.replace('_', ' ')}: {value}")
        return 0
    m = run_metrics(orc.store, args.run)
    if args.json:
        print(m.model_dump_json(indent=2))
        return 0
    print(f"Run {m.run_id}: {m.status.value}")
    print(f"Straight-through: {'yes' if m.straight_through else 'no'}   "
          f"Analyst touches: {m.analyst_touches}   Exceptions raised: {m.exceptions_raised}")
    wall = f"{m.wall_clock_seconds:.1f}s" if m.wall_clock_seconds is not None else "n/a (not complete)"
    print(f"Processing time: {m.elapsed_seconds:.3f}s   Wall clock incl. analyst waiting: {wall}")
    print(_table(["step", "agent", "status", "attempts", "seconds", "exceptions", "touches"],
                 [[s.number, s.name, s.status.value, s.attempts, f"{s.elapsed_seconds:.3f}", s.exceptions_raised,
                   s.analyst_touches] for s in m.steps]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forty8", description="Forty8 catastrophe-modelling pipeline")
    parser.add_argument("--config", help="path to forty8.toml (default: FORTY8_CONFIG or ./config/forty8.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("synth", help="generate a synthetic submission")
    p.add_argument("--profile", choices=["clean", "messy"], default="clean")
    p.add_argument("--out", default="synthetic")
    p.add_argument("--seed", type=int, default=48)
    p.add_argument("--sov-format", choices=["xlsx", "csv"], default="xlsx")
    p.add_argument("--slip-format", choices=["docx", "pdf"], default="docx")
    p.set_defaults(func=cmd_synth)

    p = sub.add_parser("run", help="run a submission through the pipeline")
    p.add_argument("sov")
    p.add_argument("slip")
    p.add_argument("--broker", default="unknown")
    p.add_argument("--insured", help="insured name (otherwise read from the slip)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="list runs, or show one run's steps")
    p.add_argument("run", nargs="?")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("review", help="work the review queue")
    rs = p.add_subparsers(dest="action", required=True)
    a = rs.add_parser("list")
    a.add_argument("--run")
    a.add_argument("--all", action="store_true", help="include resolved items")
    a = rs.add_parser("show")
    a.add_argument("id", type=int)
    a = rs.add_parser("correct")
    a.add_argument("id", type=int)
    a.add_argument("--value", required=True, help="the correction (JSON or plain text)")
    a.add_argument("--analyst", required=True)
    a = rs.add_parser("resume")
    a.add_argument("run_id")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("learn", help="learned mappings: list, approve (second analyst), revoke")
    ls = p.add_subparsers(dest="action", required=True)
    a = ls.add_parser("list")
    a.add_argument("--status", choices=[s.value for s in MappingStatus])
    a = ls.add_parser("approve")
    a.add_argument("id", type=int)
    a.add_argument("--analyst", required=True)
    a.add_argument("--scope", choices=["global", "broker"], default="global")
    a = ls.add_parser("revoke")
    a.add_argument("id", type=int)
    a.add_argument("--analyst", required=True)
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("audit", help="show a run's audit log")
    p.add_argument("run")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("report", help="run metrics report (markdown)")
    p.add_argument("run")
    p.add_argument("--out", help="write the report to this file")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("export", help="write the model-ready exposure set as OED location and account CSV")
    p.add_argument("run")
    p.add_argument("--out", default="export")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("serve", help="start the web page (run progress, review queue, audit log)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8048)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("metrics", help="show a run's metrics, or a summary across all runs")
    p.add_argument("run", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_metrics)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (KeyError, ValueError, PermissionError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
