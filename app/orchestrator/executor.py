"""Safe bridge from an LLM tool choice to a recon subprocess.

The planner may only name a capability from the registry; this module is the one
place that turns that name into an actual process. It:

  * refuses any name not in the registry (no arbitrary commands),
  * passes only fixed, validated arguments (host/program/platform/output-dir) —
    never free-form strings from the model,
  * runs the module from the recon repo root so its ``scopes/`` and
    ``scripts.utils`` imports resolve, with scope enforced by the module itself,
  * injects the recon tool binaries onto PATH and forces UTF-8 I/O,
  * reports which artifacts appeared so the planner sees real effects.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .tool_registry import ReconTool, get_tool

# hackbot repo root (…/app/orchestrator/executor.py -> parents[2]).
_HACKBOT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECON_REPO = _HACKBOT_ROOT / "external" / "auto-bug-bounty"
# Project default install location for recon binaries (subfinder, httpx, nuclei, ffuf,
# katana). ~/recon-tools resolves per-user (e.g. C:\Users\<you>\recon-tools) so it need
# not be passed on every run. Override with --tools-dir or HACKBOT_RECON_TOOLS_DIR.
DEFAULT_RECON_TOOLS_DIR = Path.home() / "recon-tools"


@dataclass
class ToolResult:
    tool: str
    ok: bool
    returncode: int
    stdout_tail: str
    stderr_tail: str
    new_artifacts: list[str]
    skipped_reason: str = ""


@dataclass
class ExploitRun:
    engine: str
    ok: bool
    returncode: int
    stdout_tail: str
    out_dir: str
    flags: list[str]
    error: str = ""


def _tail(text: str, lines: int = 12) -> str:
    rows = [r for r in (text or "").splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


def host_result_dir(recon_repo: Path, output_dir: str, host: str, program: str) -> Path:
    """Resolve the per-host result dir the way the recon modules do.

    Uses the recon repo's own ``flat_host_dir`` when importable (authoritative),
    and falls back to the observed ``<output_dir>/<program>/<host>`` layout.
    """
    repo = str(recon_repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from scripts.utils.result_paths import flat_host_dir  # type: ignore

        base = Path(output_dir)
        if not base.is_absolute():
            base = recon_repo / base
        return flat_host_dir(base, host, program)
    except Exception:
        base = Path(output_dir)
        if not base.is_absolute():
            base = recon_repo / base
        return base / program / host


def _artifact_set(result_dir: Path) -> set[str]:
    if not result_dir.exists():
        return set()
    return {p.name for p in result_dir.iterdir() if p.is_file()}


class ReconExecutor:
    def __init__(
        self,
        host: str,
        program: str,
        platform: str,
        output_dir: str = "results",
        recon_repo: Path | None = None,
        tools_dir: str | None = None,
        python_executable: str | None = None,
        step_timeout_seconds: int = 900,
    ) -> None:
        self.host = host
        self.program = program
        self.platform = platform
        self.output_dir = output_dir
        self.recon_repo = Path(recon_repo or os.getenv("HACKBOT_RECON_REPO", DEFAULT_RECON_REPO)).resolve()
        # Resolve tools dir: explicit arg > env var > project default (~/recon-tools if it
        # exists). Normalized to a native absolute path so a bash-style "/c/..." or "~"
        # can't slip onto PATH where Windows Python won't understand it.
        chosen = tools_dir or os.getenv("HACKBOT_RECON_TOOLS_DIR") or (
            str(DEFAULT_RECON_TOOLS_DIR) if DEFAULT_RECON_TOOLS_DIR.is_dir() else ""
        )
        self.tools_dir = str(Path(chosen).expanduser().resolve()) if chosen else ""
        self.python = python_executable or sys.executable
        self.step_timeout_seconds = step_timeout_seconds

    # -- preflight -----------------------------------------------------------
    def scope_file_exists(self) -> bool:
        scope = self.recon_repo / "scopes" / self.platform / f"{self.program}.json"
        return scope.exists()

    def result_dir(self) -> Path:
        return host_result_dir(self.recon_repo, self.output_dir, self.host, self.program)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"  # recon modules also self-fix, belt-and-suspenders
        env["PYTHONIOENCODING"] = "utf-8"
        if self.tools_dir:
            env["PATH"] = f"{self.tools_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    # -- execution -----------------------------------------------------------
    def run(self, tool_name: str) -> ToolResult:
        tool = get_tool(tool_name)
        if tool is None:
            return ToolResult(tool_name, False, -1, "", "unknown tool (not in registry)", [])

        result_dir = self.result_dir()
        before = _artifact_set(result_dir)

        argv = [
            self.python,
            str(self.recon_repo / tool.module),
            "--host", self.host,
            "--program", self.program,
            "--platform", self.platform,
            "--output-dir", self.output_dir,
        ]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.recon_repo),
                env=self._env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.step_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name, False, -1, "", f"timed out after {self.step_timeout_seconds}s", []
            )

        after = _artifact_set(result_dir)
        new_artifacts = sorted(after - before)
        # A rerun can refresh files without adding new names; treat produced files as progress too.
        produced_now = sorted(a for a in tool.produces if a in after)
        ok = proc.returncode == 0
        return ToolResult(
            tool=tool_name,
            ok=ok,
            returncode=proc.returncode,
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
            new_artifacts=new_artifacts or produced_now,
        )

    def run_exploit_engine(self, engine, recon_dir: Path, out_dir: Path) -> ExploitRun:
        """Hand the recon results to an exploitation engine submodule."""
        from .exploit_engines import build_engine_argv

        cwd = _HACKBOT_ROOT / engine.submodule
        if not cwd.exists():
            return ExploitRun(engine.name, False, -1, "", str(out_dir), [], error=f"submodule missing: {cwd}")

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = build_engine_argv(
            engine,
            self.python,
            self.host,
            self.program,
            self.platform,
            str(Path(recon_dir).resolve()),
            str(out_dir.resolve()),
        )
        try:
            proc = subprocess.run(
                argv,
                cwd=str(cwd),
                env=self._env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.step_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ExploitRun(engine.name, False, -1, "", str(out_dir), [], error=f"timed out after {self.step_timeout_seconds}s")

        flags: list[str] = []
        flags_file = out_dir / "flags.txt"
        if flags_file.exists():
            flags = [l.strip() for l in flags_file.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        return ExploitRun(
            engine=engine.name,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout_tail=_tail(proc.stdout, 20),
            out_dir=str(out_dir),
            flags=flags,
        )
