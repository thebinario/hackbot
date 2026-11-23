"""Local agentic recon orchestrator.

The hackbot RAG already turns a recon *profile* into writeup-grounded hypotheses.
This package adds the step before that: a local LLM (qwen2.5 via Ollama) that
decides WHICH recon capabilities to run and in what order, executes them through
the auto-bug-bounty recon modules, feeds the results back, and repeats until the
recon is good enough — then hands the aggregated profile to the existing analyzer.

Design constraints (same spirit as the rest of the repo):
  * Everything runs locally: the LLM (Ollama), the RAG (Chroma) and the recon
    subprocesses all stay on the operator's machine.
  * The LLM may only pick tools from a fixed whitelist, with validated arguments.
    There is no path from a model decision to an arbitrary shell command, and no
    path to automated exploitation — this orchestrates *recon only*.
  * Scope is enforced by the recon modules' own scope_guard; the orchestrator
    additionally refuses to start without a scope file for the program.
"""

from .tool_registry import (
    RECON_TOOLS,
    ReconTool,
    capability_documents,
    get_tool,
    ollama_tool_schemas,
)

__all__ = [
    "RECON_TOOLS",
    "ReconTool",
    "capability_documents",
    "get_tool",
    "ollama_tool_schemas",
]
