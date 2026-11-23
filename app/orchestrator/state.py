"""Compact, LLM-facing view of what recon has produced so far.

Reads the per-host result dir (the same files the recon modules write and that
hunt.py later aggregates) and summarizes it: which capabilities have run, how big
the live surface is, what tech was seen, and how many crawl/fuzz/vuln signals
exist. The planner gets this every turn so its next choice is grounded in real
effects, not in what it hoped a tool would do.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tool_registry import RECON_TOOLS


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_lines(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return []


class ReconState:
    def __init__(self, result_dir: Path) -> None:
        self.result_dir = result_dir

    def completed_tools(self) -> list[str]:
        """Capabilities whose produced artifacts are all present."""
        done = []
        for tool in RECON_TOOLS:
            if tool.produces and all((self.result_dir / name).exists() for name in tool.produces):
                done.append(tool.name)
        return done

    def summary(self) -> dict[str, Any]:
        d = self.result_dir
        live = _read_lines(d / "live_hosts.txt")
        tech = _read_json(d / "tech_stack.json")
        crawl_urls = _read_lines(d / "crawl_urls.txt")
        js = _read_lines(d / "crawl_js_files.txt")
        api = _read_lines(d / "crawl_api_endpoints.txt")
        fuzz = _read_json(d / "fuzz_triage.json")
        ports = _read_json(d / "open_ports.json")
        vulns = _read_json(d / "vuln_findings.json")

        fuzz_kinds: dict[str, int] = {}
        if isinstance(fuzz, list):
            for item in fuzz:
                if isinstance(item, dict):
                    kind = str(item.get("kind", "unknown"))
                    fuzz_kinds[kind] = fuzz_kinds.get(kind, 0) + 1

        techs: list[str] = []
        if isinstance(tech, dict):
            seen: set[str] = set()
            for values in tech.values():
                if isinstance(values, list):
                    for v in values:
                        if isinstance(v, str) and v not in seen:
                            seen.add(v)
                            techs.append(v)

        return {
            "completed_tools": self.completed_tools(),
            "live_host_count": len(live),
            "live_hosts": live[:12],
            "technologies": techs[:20],
            "crawl_url_count": len(crawl_urls),
            "js_file_count": len(js),
            "api_endpoint_count": len(api),
            "fuzz_signal_counts": fuzz_kinds,
            "open_port_count": len(ports) if isinstance(ports, list) else 0,
            "vuln_finding_count": len(vulns) if isinstance(vulns, list) else 0,
        }

    def as_prompt_block(self) -> str:
        s = self.summary()
        done = ", ".join(s["completed_tools"]) or "none yet"
        techs = ", ".join(s["technologies"]) or "unknown"
        fuzz = ", ".join(f"{k}:{v}" for k, v in s["fuzz_signal_counts"].items()) or "none"
        return (
            f"Recon state so far:\n"
            f"- completed capabilities: {done}\n"
            f"- live hosts: {s['live_host_count']} ({', '.join(s['live_hosts']) or 'none'})\n"
            f"- technologies seen: {techs}\n"
            f"- crawl: {s['crawl_url_count']} URLs, {s['js_file_count']} JS files, {s['api_endpoint_count']} API endpoints\n"
            f"- fuzz signals: {fuzz}\n"
            f"- open ports: {s['open_port_count']}; vuln findings: {s['vuln_finding_count']}"
        )
