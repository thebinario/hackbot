"""Manage a local target scope — no bug-bounty platform required.

    # start a program from a seed host (adds the apex + its wildcard)
    python scripts/scope.py init --program myprog --platform hackerone --host hackycorp.com

    # after recon, review newly discovered hosts and accept/block each interactively
    python scripts/scope.py review --program myprog --platform hackerone \
        --result-dir external/auto-bug-bounty/results-agent/myprog/hackycorp.com

    python scripts/scope.py add   --program myprog --platform hackerone --host api.hackycorp.com
    python scripts/scope.py block --program myprog --platform hackerone --host mail.hackycorp.com
    python scripts/scope.py show  --program myprog --platform hackerone
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.runtime import ensure_supported_python

ensure_supported_python()

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass

from app.orchestrator.scope_manager import ScopeManager


def _read_lines(path: Path) -> list[str]:
    try:
        return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def _discovered_hosts(result_dir: Path) -> list[str]:
    hosts: list[str] = []
    hosts += _read_lines(result_dir / "all_subdomains.txt")
    # live_hosts.txt may hold URLs — reduce to hostnames.
    from urllib.parse import urlparse
    for line in _read_lines(result_dir / "live_hosts.txt"):
        hosts.append(urlparse(line).hostname or line if "://" in line else line)
    return list(dict.fromkeys(h.strip().lower() for h in hosts if h.strip()))


def cmd_init(args) -> int:
    mgr = ScopeManager.load_or_create(args.program, args.platform, args.scopes_dir)
    mgr.add_host(args.host, wildcard=args.wildcard)
    path = mgr.save()
    extra = f" (+ *.{args.host})" if args.wildcard else " — discovered subdomains will be proposed for review"
    print(f"[scope] initialized {args.program} ({args.platform}) with {args.host}{extra}")
    print(f"[scope] -> {path}")
    return 0


def cmd_add(args) -> int:
    mgr = ScopeManager.load_or_create(args.program, args.platform, args.scopes_dir)
    mgr.add_host(args.host, wildcard=args.wildcard)
    print(f"[scope] added {args.host} to in_scope -> {mgr.save()}")
    return 0


def cmd_block(args) -> int:
    mgr = ScopeManager.load_or_create(args.program, args.platform, args.scopes_dir)
    mgr.block_host(args.host)
    print(f"[scope] blocked {args.host} (out_of_scope) -> {mgr.save()}")
    return 0


def cmd_show(args) -> int:
    mgr = ScopeManager.load_or_create(args.program, args.platform, args.scopes_dir)
    print(f"# {args.program} ({args.platform}) — {mgr.path}")
    print("in_scope:")
    for e in mgr.data["in_scope"]:
        print(f"  + [{e.get('asset_type')}] {e.get('asset_identifier')}")
    print("out_of_scope (block list):")
    for e in mgr.data["out_of_scope"]:
        print(f"  - [{e.get('asset_type')}] {e.get('asset_identifier')}")
    return 0


def cmd_review(args) -> int:
    mgr = ScopeManager.load_or_create(args.program, args.platform, args.scopes_dir)
    result_dir = Path(args.result_dir)
    discovered = _discovered_hosts(result_dir)
    if not discovered:
        print(f"[scope] no discovered hosts under {result_dir} (run recon first).", file=sys.stderr)
        return 1
    proposals = mgr.propose_new(discovered)
    if not proposals:
        print("[scope] nothing new — every discovered host is already accepted or blocked.")
        return 0

    print(f"[scope] {len(proposals)} new host(s) discovered. For each: [a]dd / [b]lock / [s]kip (default skip).")
    added = blocked = 0
    for host in proposals:
        if args.yes:
            mgr.add_host(host); added += 1; print(f"  + {host} (auto-added)"); continue
        try:
            choice = input(f"  {host} ? [a/b/s] ").strip().lower()
        except EOFError:
            choice = "s"
        if choice in ("a", "add", "y", "yes"):
            mgr.add_host(host); added += 1
        elif choice in ("b", "block", "n", "no"):
            mgr.block_host(host); blocked += 1
        # anything else: skip (leave undecided for a later review)
    mgr.save()
    print(f"[scope] review done: +{added} in_scope, -{blocked} blocked, {len(proposals)-added-blocked} skipped")
    print(f"[scope] -> {mgr.path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("init", "add", "block", "show", "review"):
        sp = sub.add_parser(name)
        sp.add_argument("--program", required=True)
        sp.add_argument("--platform", default="hackerone")
        sp.add_argument("--scopes-dir", default=None, help="Scope store dir (default: external/auto-bug-bounty/scopes).")
        if name in ("init", "add", "block"):
            sp.add_argument("--host", required=True)
        if name in ("init", "add"):
            sp.add_argument("--wildcard", action="store_true", help="Also authorize *.<host> (all subdomains) instead of asking per discovered host.")
        if name == "review":
            sp.add_argument("--result-dir", required=True, help="Per-host recon dir with all_subdomains.txt / live_hosts.txt.")
            sp.add_argument("--yes", action="store_true", help="Non-interactive: accept every newly discovered host.")
    return p


def main() -> int:
    args = build_parser().parse_args()
    return {"init": cmd_init, "add": cmd_add, "block": cmd_block, "show": cmd_show, "review": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
