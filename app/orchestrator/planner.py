"""The local agentic recon loop.

A local LLM (qwen2.5 via Ollama) is given the target, the live recon state, and
RAG-retrieved knowledge of what each recon capability does. Each turn it calls
one whitelisted tool (Ollama tool-calling, with a JSON fallback for when the 7B
model answers in prose); the executor runs it, and the real result is fed back.
When the model calls ``finish_recon`` — or the step budget is spent, or every
capability has run — the aggregated recon profile is handed to the existing
writeups analyzer for hypotheses. Nothing here contacts the target directly and
nothing exploits anything: it only orchestrates recon.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.writeups import (
    ScenarioAnalysis,
    analyze_scenario,
    build_analysis_client,
    build_embedding_client,
)
from app.rag.vector_store import DEFAULT_CHROMA_PERSIST_DIRECTORY

from .capability_store import search_capabilities
from .executor import ReconExecutor, ToolResult
from .exploit_engines import (
    EXPLOIT_ENGINES,
    SKIP_ENGINE_NAME,
    ExploitEngine,
    get_engine,
    ollama_engine_schemas,
)
from .scope_manager import ScopeManager
from .state import ReconState
from .tool_registry import (
    FINISH_TOOL_NAME,
    RECON_TOOLS,
    get_tool,
    ollama_tool_schemas,
    tool_names,
)

DEFAULT_MAX_STEPS = 8
REVIEW_SCOPE_TOOL_NAME = "review_scope"

REVIEW_SCOPE_SCHEMA = {
    "type": "function",
    "function": {
        "name": REVIEW_SCOPE_TOOL_NAME,
        "description": (
            "Ask the operator which newly discovered hosts to add to the authorized scope "
            "(accept) or block. Call this after subdomain_enum when discovery turned up hosts "
            "that are not yet in scope, BEFORE trying to probe them — accepted hosts become "
            "in-scope and can then be re-enumerated; blocked hosts are remembered and never asked again."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

SYSTEM_PROMPT = (
    "You are a local reconnaissance planner for authorized security testing. "
    "You direct a fixed set of recon tools against an in-scope target to build the best "
    "possible picture of its attack surface. You never exploit anything and never contact "
    "the target yourself — you only choose which recon capability runs next.\n\n"
    "Rules:\n"
    "- Choose exactly one tool per turn by calling it, or call finish_recon to stop.\n"
    "- Respect dependencies: subdomain_enum first (it finds the live hosts everything else needs); "
    "tech_detect/web_crawl/dir_fuzz need live hosts; nuclei_scan needs a detected stack.\n"
    "- Do not repeat a capability that already completed unless you have a concrete reason.\n"
    "- If subdomain_enum discovers hosts that are not yet in scope, call review_scope to ask the "
    "operator which to add before probing them; after they accept, re-run subdomain_enum to probe the new hosts.\n"
    "- Prefer breadth first (surface, stack, endpoints, hidden paths), heavy/vuln steps last.\n"
    "- Finish once the surface, stack, endpoints and fuzz signals give enough to form hypotheses, "
    "or when the only remaining tools are unavailable or low-value here."
)


@dataclass
class PlannedStep:
    index: int
    tool: str
    reason: str
    executed: bool
    ok: bool
    note: str
    new_artifacts: list[str] = field(default_factory=list)


@dataclass
class AgentRun:
    host: str
    program: str
    platform: str
    steps: list[PlannedStep]
    final_state: dict[str, Any]
    capability_sources: list[str]
    analysis: ScenarioAnalysis | None
    stop_reason: str
    exploit_engine: str = ""
    exploit_reason: str = ""
    exploit_result: dict[str, Any] | None = None


def _available_binaries(tools_dir: str) -> Callable[[str], bool]:
    search_path = os.environ.get("PATH", "")
    if tools_dir:
        search_path = f"{tools_dir}{os.pathsep}{search_path}"

    def available(binary: str) -> bool:
        return shutil.which(binary, path=search_path) is not None

    return available


def _unmet_requirements(tool_name: str, completed: list[str], result_dir: Path) -> list[str]:
    tool = get_tool(tool_name)
    if tool is None:
        return []
    return [req for req in tool.requires if not (result_dir / req).exists()]


def _extract_choice(message: dict[str, Any]) -> tuple[str | None, str]:
    """Pull (tool_name, reason) from a tool call, or fall back to parsing JSON/text."""
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        fn = (tool_calls[0] or {}).get("function", {})
        name = fn.get("name")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        reason = (args or {}).get("reason", "") if isinstance(args, dict) else ""
        if isinstance(name, str):
            return name, str(reason)

    # Fallback: model replied in prose. Look for a known tool name / a JSON blob.
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        try:
            blob = json.loads(content)
            if isinstance(blob, dict) and isinstance(blob.get("tool"), str):
                return blob["tool"], str(blob.get("reason", ""))
        except json.JSONDecodeError:
            pass
        lowered = content.lower()
        for name in [*tool_names(), FINISH_TOOL_NAME, REVIEW_SCOPE_TOOL_NAME]:
            if name in lowered:
                return name, content.strip()[:200]
    return None, ""


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def concrete_leads(result_dir: Path, limit: int = 15) -> list[dict[str, Any]]:
    """Turn raw recon artifacts into URL-level leads an operator can act on now.

    These are the high-signal, concrete findings (an exposed /config returning 200,
    a directory listing, a disclosed absolute path, a nuclei hit) — not semantic
    guesses. Surfacing them verbatim is what makes the hypotheses point at the real
    entry points instead of generic vulnerability classes.
    """
    leads: list[dict[str, Any]] = []

    triage = _read_json_file(result_dir / "fuzz_triage.json")
    if isinstance(triage, list):
        for item in triage:
            if isinstance(item, dict) and item.get("url"):
                leads.append(
                    {
                        "severity": str(item.get("severity", "unknown")).lower(),
                        "kind": item.get("kind", "fuzz"),
                        "url": item.get("url"),
                        "status": item.get("status"),
                        "evidence": item.get("evidence", ""),
                    }
                )

    vulns = _read_json_file(result_dir / "vuln_findings.json")
    if isinstance(vulns, list):
        for item in vulns:
            if isinstance(item, dict):
                leads.append(
                    {
                        "severity": str(item.get("severity", "unknown")).lower(),
                        "kind": item.get("template-id") or item.get("name") or "nuclei",
                        "url": item.get("matched-at") or item.get("host") or item.get("url"),
                        "status": None,
                        "evidence": item.get("name", ""),
                    }
                )

    leads.sort(key=lambda lead: _SEVERITY_RANK.get(lead["severity"], 5))
    return leads[:limit]


def _format_lead(lead: dict[str, Any]) -> str:
    status = f" [{lead['status']}]" if lead.get("status") else ""
    evidence = f" — {lead['evidence']}" if lead.get("evidence") else ""
    return f"{lead['severity'].upper()} {lead['kind']}: {lead['url']}{status}{evidence}"


def _build_scenario(host: str, state: dict[str, Any], result_dir: Path, leads: list[dict[str, Any]]) -> str:
    stack = state.get("technologies") or []
    api = []
    try:
        api = [
            line.strip()
            for line in (result_dir / "crawl_api_endpoints.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ][:12]
    except Exception:
        api = []

    parts = [f"Host: {host}"]
    if stack:
        parts.append("Technology stack: " + "; ".join(stack))
    if api:
        parts.append("Endpoints: " + "; ".join(api))
    if leads:
        # Concrete, URL-level exposures come first: these are what a tester should open.
        parts.append(
            "Concrete exposures found during recon (verify each; these are real responses, "
            "not guesses): " + "; ".join(_format_lead(lead) for lead in leads)
        )
    else:
        fuzz = state.get("fuzz_signal_counts") or {}
        if fuzz:
            parts.append(
                "Findings / observed behavior: "
                + "; ".join(f"{v} fuzz hit(s) of kind '{k}'" for k, v in fuzz.items())
            )
    parts.append(
        f"Live hosts discovered: {state.get('live_host_count', 0)}; "
        f"crawled URLs: {state.get('crawl_url_count', 0)}; JS files: {state.get('js_file_count', 0)}."
    )
    return "\n".join(parts)


def _choose_engine(gen_client, host: str, state: dict[str, Any], leads: list[dict[str, Any]]) -> tuple[ExploitEngine | None, str]:
    """Let the LLM pick one exploitation engine (or skip) from the registry knowledge."""
    knowledge = "\n\n".join(e.capability_text() for e in EXPLOIT_ENGINES)
    lead_text = "; ".join(_format_lead(lead) for lead in leads[:10]) or "no concrete leads"
    messages = [
        {
            "role": "system",
            "content": (
                "You select at most one exploitation/validation engine to run against an in-scope "
                "target, based on what recon found. Call the engine that best fits, or skip_exploitation "
                "if none fits. Base the choice on the engine knowledge and the concrete findings."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Host: {host}\nTechnologies: {', '.join(state.get('technologies') or []) or 'unknown'}\n"
                f"Concrete findings: {lead_text}\n\nAvailable engines:\n{knowledge}\n\nChoose one engine, or skip."
            ),
        },
    ]
    try:
        message = gen_client.chat(messages, tools=ollama_engine_schemas(), options={"temperature": 0.1})
    except Exception as exc:  # fall back to no engine on any model/transport error
        return None, f"engine selection failed: {exc}"
    name, reason = _extract_choice(message if isinstance(message, dict) else {})
    if not name or name == SKIP_ENGINE_NAME:
        return None, reason or "model skipped exploitation"
    return get_engine(name), reason


def _discovered_hosts(result_dir: Path) -> list[str]:
    from urllib.parse import urlparse

    def _lines(name: str) -> list[str]:
        try:
            return [l.strip() for l in (result_dir / name).read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return []

    hosts = list(_lines("all_subdomains.txt"))
    for line in _lines("live_hosts.txt"):
        hosts.append((urlparse(line).hostname or line) if "://" in line else line)
    return list(dict.fromkeys(h.strip().lower() for h in hosts if h.strip()))


def _stdin_ask(host: str) -> str:
    try:
        return input(f"  add to scope? {host}  [a]dd / [b]lock / [s]kip (default skip): ")
    except EOFError:
        return "s"


def _review_scope(
    executor: ReconExecutor,
    program: str,
    platform: str,
    result_dir: Path,
    ask_user: Callable[[str], str] | None,
    log: Callable[[str], None],
) -> tuple[str, int]:
    """Interactively accept/block newly discovered hosts. Returns (summary, n_added)."""
    mgr = ScopeManager.load_or_create(program, platform, executor.recon_repo / "scopes")
    proposals = mgr.propose_new(_discovered_hosts(result_dir))
    if not proposals:
        return "review_scope: no new hosts — every discovered host is already accepted or blocked.", 0
    ask = ask_user or _stdin_ask
    log(f"[review_scope] {len(proposals)} new host(s) to review")
    added = blocked = skipped = 0
    for host in proposals:
        choice = (ask(host) or "s").strip().lower()[:1]
        if choice in ("a", "y"):
            mgr.add_host(host)
            added += 1
        elif choice in ("b", "n"):
            mgr.block_host(host)
            blocked += 1
        else:
            skipped += 1
    mgr.save()
    summary = (
        f"review_scope: +{added} added to scope, -{blocked} blocked, {skipped} skipped. "
        + ("Re-run subdomain_enum to probe the newly added hosts." if added else "Scope unchanged for probing.")
    )
    log(f"[review_scope] {summary}")
    return summary, added


def run_recon_agent(
    host: str,
    program: str,
    platform: str,
    output_dir: str = "results",
    *,
    tools_dir: str | None = None,
    recon_repo: Path | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    top_k: int = 4,
    exploit_engine: str = "auto",
    persist_directory: str | Path = DEFAULT_CHROMA_PERSIST_DIRECTORY,
    on_event: Callable[[str], None] | None = None,
    ask_user: Callable[[str], str] | None = None,
) -> AgentRun:
    log = on_event or (lambda _m: None)

    embed_client = build_embedding_client()
    gen_client = build_analysis_client()
    executor = ReconExecutor(
        host=host,
        program=program,
        platform=platform,
        output_dir=output_dir,
        recon_repo=recon_repo,
        tools_dir=tools_dir,
    )
    if not executor.scope_file_exists():
        raise RuntimeError(
            f"no scope file for program '{program}' on platform '{platform}' in "
            f"{executor.recon_repo / 'scopes'} — refusing to run recon out of scope"
        )

    result_dir = executor.result_dir()
    result_dir.mkdir(parents=True, exist_ok=True)
    is_available = _available_binaries(executor.tools_dir)

    # RAG: retrieve capability knowledge for this target (fall back to full manifest).
    cap_query = (
        f"recon planning for {host} on {platform}: which tools to enumerate subdomains, "
        f"detect technology stack, crawl endpoints, fuzz hidden paths, scan ports and vulns"
    )
    cap_hits = search_capabilities(cap_query, embed_client, persist_directory=persist_directory, top_k=6)
    if cap_hits:
        capability_knowledge = "\n\n".join(text for text, _ in cap_hits)
        capability_sources = ["recon_capabilities (RAG)"]
    else:
        capability_knowledge = "\n\n".join(t.capability_text() for t in RECON_TOOLS)
        capability_sources = ["tool_registry (manifest fallback)"]

    # Note which binaries are missing so the planner avoids dead steps.
    missing = sorted(
        {b for t in RECON_TOOLS for b in t.binaries if not is_available(b)}
    )
    avail_note = (
        f"Unavailable external tools (capabilities needing them will be skipped): {', '.join(missing)}."
        if missing
        else "All external recon tools are available."
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Target in scope: {host} (program {program}, platform {platform}).\n\n"
                f"What the available recon capabilities do (retrieved knowledge):\n{capability_knowledge}\n\n"
                f"{avail_note}\n\nBegin recon planning."
            ),
        },
    ]

    tool_schemas = ollama_tool_schemas() + [REVIEW_SCOPE_SCHEMA]
    steps: list[PlannedStep] = []
    stop_reason = "max_steps"
    noop_streak = 0          # consecutive steps that ran nothing (skip/block/error)
    subdomain_reruns = 0     # force at most one re-run of the gating discovery step
    scope_changed = False    # review_scope added hosts → allow re-probing via subdomain_enum
    MAX_NOOP_STEPS = 3       # bail out instead of spinning to max_steps on no progress

    for step_index in range(1, max_steps + 1):
        state = ReconState(result_dir)
        completed = state.completed_tools()
        if len(completed) >= len(RECON_TOOLS):
            stop_reason = "all_capabilities_completed"
            break

        messages.append(
            {
                "role": "user",
                "content": state.as_prompt_block()
                + "\n\nCall the single best next recon capability, or finish_recon if recon is sufficient.",
            }
        )
        message = gen_client.chat(messages, tools=tool_schemas, options={"temperature": 0.2})
        messages.append(message if isinstance(message, dict) else {"role": "assistant", "content": str(message)})

        name, reason = _extract_choice(message if isinstance(message, dict) else {})
        if name is None:
            log(f"[step {step_index}] model gave no actionable choice; stopping.")
            stop_reason = "no_choice"
            break

        if name == FINISH_TOOL_NAME:
            log(f"[step {step_index}] finish_recon: {reason}")
            steps.append(PlannedStep(step_index, name, reason, False, True, "model finished recon"))
            stop_reason = "finished_by_model"
            break

        if name == REVIEW_SCOPE_TOOL_NAME:
            summary, n_added = _review_scope(executor, program, platform, result_dir, ask_user, log)
            if n_added:
                scope_changed = True
            steps.append(PlannedStep(step_index, name, reason, True, True, summary))
            messages.append({"role": "tool", "tool_name": name, "content": summary})
            noop_streak = 0
            continue

        tool = get_tool(name)
        if tool is None:
            note = f"ignored unknown tool '{name}'"
            log(f"[step {step_index}] {note}")
            steps.append(PlannedStep(step_index, name, reason, False, False, note))
            messages.append({"role": "tool", "tool_name": name, "content": f"ERROR: {note}. Pick a valid capability."})
            noop_streak += 1
            if noop_streak >= MAX_NOOP_STEPS:
                log(f"[step {step_index}] no progress for {noop_streak} steps; stopping.")
                stop_reason = "no_progress"
                break
            continue

        # Guard: already done?
        if name in completed:
            # Exception: the discovery step "completed" but found no live hosts (e.g. a
            # prior run with mis-configured tools). Everything downstream is dead without
            # it, so force exactly one re-run instead of skipping forever.
            no_live_rerun = (
                name == "subdomain_enum"
                and state.summary().get("live_host_count", 0) == 0
                and subdomain_reruns == 0
            )
            scope_rerun = name == "subdomain_enum" and scope_changed
            if no_live_rerun or scope_rerun:
                if scope_rerun:
                    scope_changed = False
                    log(f"[step {step_index}] {name}: scope changed — re-running to probe newly added hosts")
                else:
                    subdomain_reruns += 1
                    log(f"[step {step_index}] {name}: completed but 0 live hosts — forcing one re-run")
            else:
                note = "capability already completed; not re-running"
                log(f"[step {step_index}] {name}: {note}")
                steps.append(PlannedStep(step_index, name, reason, False, True, note))
                messages.append({"role": "tool", "tool_name": name, "content": f"SKIPPED: {note}. Pick a DIFFERENT capability or finish_recon."})
                noop_streak += 1
                if noop_streak >= MAX_NOOP_STEPS:
                    log(f"[step {step_index}] no progress for {noop_streak} steps; stopping.")
                    stop_reason = "no_progress"
                    break
                continue

        # Guard: unmet dependencies?
        unmet = _unmet_requirements(name, completed, result_dir)
        if unmet:
            note = f"needs these first: {', '.join(unmet)}"
            log(f"[step {step_index}] {name}: {note}")
            steps.append(PlannedStep(step_index, name, reason, False, False, note))
            messages.append(
                {"role": "tool", "tool_name": name, "content": f"BLOCKED: {note}. Run the prerequisite capability first."}
            )
            noop_streak += 1
            if noop_streak >= MAX_NOOP_STEPS:
                log(f"[step {step_index}] no progress for {noop_streak} steps; stopping.")
                stop_reason = "no_progress"
                break
            continue

        # Guard: missing binaries?
        unavailable = [b for b in tool.binaries if not is_available(b)]
        if unavailable and all(b in unavailable for b in tool.binaries):
            note = f"required tool(s) not installed: {', '.join(unavailable)}"
            log(f"[step {step_index}] {name}: {note}")
            steps.append(PlannedStep(step_index, name, reason, False, False, note))
            messages.append(
                {"role": "tool", "tool_name": name, "content": f"UNAVAILABLE: {note}. Choose a different capability or finish."}
            )
            noop_streak += 1
            if noop_streak >= MAX_NOOP_STEPS:
                log(f"[step {step_index}] no progress for {noop_streak} steps; stopping.")
                stop_reason = "no_progress"
                break
            continue

        log(f"[step {step_index}] running {name} — {reason}")
        noop_streak = 0  # a real run happened — reset the no-progress counter
        result: ToolResult = executor.run(name)
        note = (
            f"ok, new/updated artifacts: {', '.join(result.new_artifacts) or 'none'}"
            if result.ok
            else f"failed (rc={result.returncode}): {result.skipped_reason or result.stderr_tail[:160]}"
        )
        log(f"[step {step_index}] {name}: {note}")
        steps.append(
            PlannedStep(step_index, name, reason, True, result.ok, note, result.new_artifacts)
        )
        feedback = {
            "tool": name,
            "ok": result.ok,
            "new_artifacts": result.new_artifacts,
            "stdout_tail": result.stdout_tail[-500:],
        }
        messages.append({"role": "tool", "tool_name": name, "content": json.dumps(feedback, ensure_ascii=False)})

    # Final: aggregate state into a scenario and ask the writeups RAG for hypotheses.
    final_state = ReconState(result_dir).summary()
    leads = concrete_leads(result_dir)
    final_state["concrete_leads"] = leads
    analysis: ScenarioAnalysis | None = None
    if final_state.get("live_host_count"):
        scenario = _build_scenario(host, final_state, result_dir, leads)
        log(f"[final] {len(leads)} concrete lead(s); generating writeup-grounded hypotheses")
        analysis = analyze_scenario(
            scenario, embed_client, gen_client, persist_directory=persist_directory, top_k=top_k
        )
    else:
        log("[final] no live hosts collected; skipping hypothesis generation")

    # Optional hand-off to an exploitation engine (LLM-chosen when exploit_engine == "auto").
    engine_name = ""
    engine_reason = ""
    exploit_result: dict[str, Any] | None = None
    if exploit_engine != "none" and final_state.get("live_host_count"):
        if exploit_engine == "auto":
            engine, engine_reason = _choose_engine(gen_client, host, final_state, leads)
        else:
            engine = get_engine(exploit_engine)
            engine_reason = f"forced via --exploit-engine {exploit_engine}"
            if engine is None:
                engine_reason = f"unknown engine '{exploit_engine}'; skipping"
        if engine is not None:
            engine_name = engine.name
            log(f"[exploit] {engine.name}: {engine_reason}")
            run = executor.run_exploit_engine(engine, result_dir, result_dir / f"exploit_{engine.name}")
            exploit_result = {
                "engine": run.engine,
                "ok": run.ok,
                "returncode": run.returncode,
                "flags": run.flags,
                "out_dir": run.out_dir,
                "error": run.error,
                "stdout_tail": run.stdout_tail,
            }
            summary = f"ok, {len(run.flags)} flag(s)" if run.ok else f"failed ({run.error or 'rc=' + str(run.returncode)})"
            log(f"[exploit] {engine.name}: {summary}")
        else:
            log(f"[exploit] no engine run: {engine_reason}")

    return AgentRun(
        host=host,
        program=program,
        platform=platform,
        steps=steps,
        final_state=final_state,
        capability_sources=capability_sources,
        analysis=analysis,
        stop_reason=stop_reason,
        exploit_engine=engine_name,
        exploit_reason=engine_reason,
        exploit_result=exploit_result,
    )
