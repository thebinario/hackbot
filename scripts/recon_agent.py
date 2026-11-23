from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime import ensure_supported_python

ensure_supported_python()

# Windows defaults stdout to cp1252, which cannot encode the arrows/dashes this
# script and the planner print. Force UTF-8 so progress output never crashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass

from app.orchestrator.executor import DEFAULT_RECON_REPO, ReconExecutor
from app.orchestrator.planner import AgentRun, run_recon_agent

PERSIST_DIRECTORY = REPO_ROOT / "data" / "vectorstore" / "chroma"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Local agentic recon: a local LLM picks and runs recon capabilities against an "
            "in-scope target, then produces writeup-grounded hypotheses. Everything runs locally."
        )
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--program", required=True)
    parser.add_argument("--platform", required=True, help="hackerone | intigriti | lab")
    parser.add_argument("--output-dir", default="results", help="Recon results dir (relative to the recon repo).")
    parser.add_argument("--tools-dir", default=None, help="Dir with recon binaries (subfinder, httpx, ...) to prepend to PATH.")
    parser.add_argument("--recon-repo", default=None, help=f"Path to auto-bug-bounty (default: {DEFAULT_RECON_REPO}).")
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument(
        "--exploit-engine",
        default="auto",
        choices=["auto", "auto-exploit", "auto-vuln", "none"],
        help="Exploitation engine after recon: 'auto' lets the LLM choose, or force one, or 'none'.",
    )
    return parser


def build_report(run: AgentRun) -> str:
    lines = [
        f"# Recon Agent Run — {run.host}",
        "",
        f"- Program: {run.program} ({run.platform})",
        f"- Capability knowledge source: {', '.join(run.capability_sources)}",
        f"- Stop reason: {run.stop_reason}",
        "",
        "## Steps the planner chose",
        "",
    ]
    for step in run.steps:
        status = "✓" if step.ok else "✗"
        ran = "ran" if step.executed else "skipped"
        lines.append(f"{step.index}. **{step.tool}** [{ran}] {status} — {step.note}")
        if step.reason:
            lines.append(f"   - reason: {step.reason}")
    leads = run.final_state.get("concrete_leads") or []
    if leads:
        lines += ["", "## Concrete leads (verify these first)", ""]
        for lead in leads:
            status = f" [{lead['status']}]" if lead.get("status") else ""
            lines.append(
                f"- **{str(lead.get('severity', '')).upper()}** {lead.get('kind')}: {lead.get('url')}{status}"
            )
    lines += ["", "## Final recon state", "", "```json", json.dumps(run.final_state, indent=2, ensure_ascii=False), "```", ""]
    if run.exploit_engine or run.exploit_reason:
        lines += ["## Exploitation engine", ""]
        lines.append(f"- Engine: **{run.exploit_engine or 'none'}** — {run.exploit_reason}")
        if run.exploit_result:
            r = run.exploit_result
            status = "ok" if r.get("ok") else f"failed ({r.get('error') or 'rc=' + str(r.get('returncode'))})"
            lines.append(f"- Result: {status}; output: {r.get('out_dir')}")
            for flag in r.get("flags") or []:
                lines.append(f"  - 🏁 `{flag}`")
        lines.append("")
    if run.analysis is not None:
        lines += ["## Writeup-grounded hypotheses", "", run.analysis.answer, "", "### Reports used", ""]
        for report, distance in zip(run.analysis.reports, run.analysis.distances):
            lines.append(
                f"- {report.get('title')} | {report.get('vuln_class')} | distance={distance:.3f} | {report.get('source_path')}"
            )
    else:
        lines += ["## Writeup-grounded hypotheses", "", "_No live hosts collected; skipped._"]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = build_parser().parse_args()

    run = run_recon_agent(
        host=args.host,
        program=args.program,
        platform=args.platform,
        output_dir=args.output_dir,
        tools_dir=args.tools_dir,
        recon_repo=Path(args.recon_repo) if args.recon_repo else None,
        max_steps=args.max_steps,
        top_k=args.top_k,
        exploit_engine=args.exploit_engine,
        persist_directory=PERSIST_DIRECTORY,
        on_event=lambda m: print(m, flush=True),
    )

    executor = ReconExecutor(
        host=args.host,
        program=args.program,
        platform=args.platform,
        output_dir=args.output_dir,
        recon_repo=Path(args.recon_repo) if args.recon_repo else None,
    )
    result_dir = executor.result_dir()
    result_dir.mkdir(parents=True, exist_ok=True)

    run_json = {
        "host": run.host,
        "program": run.program,
        "platform": run.platform,
        "stop_reason": run.stop_reason,
        "capability_sources": run.capability_sources,
        "steps": [vars(s) for s in run.steps],
        "final_state": run.final_state,
        "exploit_engine": run.exploit_engine,
        "exploit_reason": run.exploit_reason,
        "exploit_result": run.exploit_result,
        "analysis": (
            {
                "answer": run.analysis.answer,
                "retrieval_query": run.analysis.retrieval_query,
                "reports": run.analysis.reports,
            }
            if run.analysis is not None
            else None
        ),
    }
    (result_dir / "agent_run.json").write_text(json.dumps(run_json, indent=2, ensure_ascii=False), encoding="utf-8")
    (result_dir / "agent_report.md").write_text(build_report(run), encoding="utf-8")

    print(f"\n[recon-agent] {len([s for s in run.steps if s.executed])} capability run(s); stop: {run.stop_reason}")
    print(f"[recon-agent] report → {result_dir / 'agent_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
