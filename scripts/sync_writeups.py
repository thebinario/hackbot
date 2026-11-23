from __future__ import annotations

import argparse
import os
import subprocess
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

from app.writeups.hackerone import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_DISCOVERY_QUERY,
    MIN_DELAY_SECONDS,
    discover_disclosed_report_ids,
    fetch_reports,
)

DEFAULT_OUTPUT_DIRECTORY = REPO_ROOT / "data" / "raw" / "writeups"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Autonomously grow the local writeups knowledge base in one command: discover "
            "disclosed HackerOne reports from the public hacktivity feed, download them, and "
            "index them — no report ids needed."
        )
    )
    parser.add_argument("--count", type=int, default=100, help="How many disclosed reports to discover and fetch. Default 100.")
    parser.add_argument("--query", default=DEFAULT_DISCOVERY_QUERY, help=f"Hacktivity filter. Default {DEFAULT_DISCOVERY_QUERY!r} (e.g. 'disclosed:true severity:critical').")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help=f"Seconds between requests (min {MIN_DELAY_SECONDS}).")
    parser.add_argument("--force", action="store_true", help="Re-download reports already on disk.")
    parser.add_argument("--no-ingest", action="store_true", help="Only discover+download; skip indexing.")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    print(f"[sync] discovering up to {args.count} disclosed report(s) from hacktivity (query: {args.query!r})", flush=True)
    try:
        ids = discover_disclosed_report_ids(args.count, query=args.query, delay_seconds=args.delay)
    except Exception as exc:
        print(f"[sync] discovery failed: {exc}", file=sys.stderr)
        return 1
    if not ids:
        print("[sync] no disclosed reports discovered (query too narrow, or the feed changed).", file=sys.stderr)
        return 1
    print(f"[sync] discovered {len(ids)} report id(s); downloading into {args.output_dir}", flush=True)

    summary = fetch_reports(ids, args.output_dir, delay_seconds=args.delay, max_reports=len(ids), force=args.force)
    print(
        f"[sync] download: saved={len(summary.saved)} already_had={len(summary.skipped_existing)} "
        f"unavailable={len(summary.unavailable)} failed={len(summary.failed)}"
    )
    if summary.stopped_reason:
        print(f"[sync] stopped early: {summary.stopped_reason}")

    if args.no_ingest:
        print("[sync] skipping indexing (--no-ingest). Run scripts/ingest_writeups.py when ready.")
        return 0

    print("[sync] indexing new reports ...", flush=True)
    ingest_env = dict(os.environ)
    ingest_env["PYTHONUTF8"] = "1"
    ingest_env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "ingest_writeups.py")], env=ingest_env)
    if result.returncode != 0:
        print("[sync] indexing step failed; see output above.", file=sys.stderr)
        return result.returncode
    print("[sync] done. Knowledge base updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
