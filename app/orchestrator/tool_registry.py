"""Structured manifest of the recon capabilities the agent may drive.

This is the *executable contract* half of the hybrid design: it says, for each
capability, exactly which auto-bug-bounty module runs it, which artifacts it
produces, what must exist first, and which external binaries it needs. The
``description`` / ``when_to_use`` text is also what gets indexed into the RAG so
the planner LLM can retrieve "what recon can do" while reasoning.

Adding a capability here (plus re-indexing) is all it takes to expose a new
recon step to the agent — no planner changes required.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReconTool:
    name: str
    """Stable id the LLM calls and the executor dispatches on."""

    module: str
    """Path (relative to the recon repo root) of the module that runs it."""

    summary: str
    """One line shown in the planner's tool list."""

    when_to_use: str
    """Guidance indexed into the RAG: when this step pays off, what it reveals."""

    produces: tuple[str, ...]
    """Artifact filenames (in the host result dir) that signal this step ran."""

    requires: tuple[str, ...] = ()
    """Artifacts that should exist first; the planner is told about unmet needs."""

    binaries: tuple[str, ...] = ()
    """External CLI tools the module shells out to (informational + preflight)."""

    cost: str = "medium"
    """Rough wall-clock/traffic cost: low | medium | high."""

    def capability_text(self) -> str:
        """The document indexed into the RAG capability collection."""
        needs = ", ".join(self.requires) if self.requires else "nothing (entry point)"
        bins = ", ".join(self.binaries) if self.binaries else "none"
        return (
            f"Recon capability: {self.name}\n"
            f"Summary: {self.summary}\n"
            f"When to use: {self.when_to_use}\n"
            f"Produces: {', '.join(self.produces)}\n"
            f"Requires first: {needs}\n"
            f"External tools: {bins}\n"
            f"Cost: {self.cost}"
        )


RECON_TOOLS: tuple[ReconTool, ...] = (
    ReconTool(
        name="subdomain_enum",
        module="scripts/recon/subdomain_enum.py",
        summary="Enumerate subdomains and probe which are live (subfinder + httpx).",
        when_to_use=(
            "Almost always the first step. Expands a single host into the live attack "
            "surface, verifies which hosts answer HTTP(S), and records server/title/tech "
            "hints. Everything else (crawl, fuzz, tech, ports) works off the live hosts "
            "it discovers. Run it before any other capability."
        ),
        produces=("live_hosts.txt", "live_hosts.json", "all_subdomains.txt"),
        requires=(),
        binaries=("subfinder", "httpx"),
        cost="medium",
    ),
    ReconTool(
        name="tech_detect",
        module="scripts/recon/tech_detect.py",
        summary="Fingerprint the technology stack per live host (nuclei tech templates + whatweb).",
        when_to_use=(
            "Run after live hosts are known. Reveals server software and versions "
            "(e.g. Apache/PHP end-of-life), CMS/framework, and WAF presence. The tech "
            "families it finds are what make later hypotheses specific instead of generic."
        ),
        produces=("tech_stack.json", "tech_nuclei.json"),
        requires=("live_hosts.txt",),
        binaries=("nuclei", "whatweb"),
        cost="medium",
    ),
    ReconTool(
        name="web_crawl",
        module="scripts/recon/web_crawl.py",
        summary="Crawl live hosts for URLs, JS files and API endpoints (katana).",
        when_to_use=(
            "Run after live hosts are known. Surfaces real routes, forms, JS bundles and "
            "API endpoints to test, and feeds the fuzzer with observed paths. Strongest "
            "signal for application-layer bugs (IDOR, injection, exposed endpoints)."
        ),
        produces=("crawl_urls.txt", "crawl_js_files.txt", "crawl_api_endpoints.txt"),
        requires=("live_hosts.txt",),
        binaries=("katana",),
        cost="medium",
    ),
    ReconTool(
        name="dir_fuzz",
        module="scripts/recon/dir_fuzz.py",
        summary="Fuzz for hidden paths: admin panels, config files, upload surfaces (ffuf).",
        when_to_use=(
            "Run after live hosts (and ideally a crawl) exist. Finds unlinked paths that "
            "triage into admin surfaces, potential config/backup exposure and upload "
            "surfaces — high-value starting points for manual testing."
        ),
        produces=("fuzz_triage.json",),
        requires=("live_hosts.txt",),
        binaries=("ffuf",),
        cost="high",
    ),
    ReconTool(
        name="port_scan",
        module="scripts/recon/port_scan.py",
        summary="Scan common web/service ports per host (nmap -sV).",
        when_to_use=(
            "Optional. Use when you suspect non-standard services (admin consoles on 8080/"
            "8443, dev servers, etc.) beyond the main web port. Requires nmap installed; "
            "skip if nmap is unavailable rather than blocking the run."
        ),
        produces=("open_ports.json",),
        requires=("live_hosts.txt",),
        binaries=("nmap",),
        cost="high",
    ),
    ReconTool(
        name="nuclei_scan",
        module="scripts/recon/nuclei_scan.py",
        summary="Run focused nuclei vulnerability templates once a stack is known.",
        when_to_use=(
            "Only after tech_detect, so templates match the observed stack. Produces "
            "concrete vulnerability signals (vuln_findings) rather than broad spraying. "
            "Heaviest step; run last and only when there is a stack to target."
        ),
        produces=("vuln_findings.json",),
        requires=("tech_stack.json",),
        binaries=("nuclei",),
        cost="high",
    ),
)


_BY_NAME = {tool.name: tool for tool in RECON_TOOLS}

#: Name the planner uses to declare recon is sufficient and stop the loop.
FINISH_TOOL_NAME = "finish_recon"


def get_tool(name: str) -> ReconTool | None:
    return _BY_NAME.get(name)


def tool_names() -> list[str]:
    return [tool.name for tool in RECON_TOOLS]


def capability_documents() -> list[dict[str, str]]:
    """(id, text) docs for indexing capabilities into the RAG."""
    return [{"id": f"capability::{t.name}", "text": t.capability_text()} for t in RECON_TOOLS]


def ollama_tool_schemas() -> list[dict[str, object]]:
    """JSON tool schemas for Ollama /api/chat tool-calling.

    Every recon tool takes no free-form arguments — the host/program/platform are
    fixed for the whole run and supplied by the orchestrator, not the model. The
    model only chooses *which* capability to run. ``finish_recon`` ends the loop.
    """
    schemas: list[dict[str, object]] = []
    for tool in RECON_TOOLS:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"{tool.summary} {tool.when_to_use}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "Why this capability is the best next step given the current recon state.",
                            }
                        },
                        "required": ["reason"],
                    },
                },
            }
        )
    schemas.append(
        {
            "type": "function",
            "function": {
                "name": FINISH_TOOL_NAME,
                "description": (
                    "Declare that recon is sufficient to generate hypotheses. Call this when the "
                    "live surface, technology stack, crawled endpoints and fuzz signals together give "
                    "enough to reason about, or when remaining steps would add little."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Why the collected recon is enough to stop.",
                        }
                    },
                    "required": ["reason"],
                },
            },
        }
    )
    return schemas
