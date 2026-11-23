from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request


REPORT_URL_TEMPLATE = "https://hackerone.com/reports/{report_id}"
USER_AGENT = "rag-cyber-frameworks-local-research/1.0 (personal study; low rate)"

MIN_DELAY_SECONDS = 1.0
DEFAULT_DELAY_SECONDS = 3.0
DEFAULT_MAX_REPORTS = 100
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 5.0
MAX_CONSECUTIVE_BLOCKS = 3

_REPORT_ID_PATTERN = re.compile(r"^(?:https?://(?:www\.)?hackerone\.com/reports/)?(\d{1,10})(?:[/?#.].*)?$")

# Called as opener(req, timeout=...): urlopen's second positional parameter is `data`, not timeout.
Opener = Callable[..., Any]


class FetchError(Exception):
    """Base class for fetch problems."""


class ReportUnavailable(FetchError):
    """The report is private, removed, or not disclosed."""


class RateLimited(FetchError):
    """HackerOne asked us to slow down. The whole run must stop."""


class BlockedByServer(FetchError):
    """Repeated refusals that look like a block rather than a private report."""


@dataclass(frozen=True)
class FetchSummary:
    saved: list[str]
    skipped_existing: list[str]
    unavailable: list[str]
    failed: list[str]
    stopped_reason: str = ""


def parse_report_ref(text: str) -> int:
    """Accept '2119149' or 'https://hackerone.com/reports/2119149[...]' and return the numeric id."""
    match = _REPORT_ID_PATTERN.match(text.strip())
    if match is None:
        raise ValueError(f"not a HackerOne report id or URL: {text!r}")
    return int(match.group(1))


def read_report_refs(path: str | Path) -> list[int]:
    """One id/URL per line; blank lines and '#' comments are ignored. Duplicates are dropped, order kept."""
    ids: list[int] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        report_id = parse_report_ref(line)
        if report_id not in ids:
            ids.append(report_id)
    return ids


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value not in (None, "") else ""


def report_to_markdown(report_id: int, data: dict[str, Any]) -> str:
    """Render a disclosed report as Markdown. Raises ReportUnavailable if there is nothing public to use."""
    if not data.get("disclosed_at"):
        raise ReportUnavailable("report is not publicly disclosed")

    body = str(data.get("vulnerability_information") or "").strip()
    if not body:
        raise ReportUnavailable("report has no public body text")

    title = _text(data.get("title")) or f"HackerOne report {report_id}"
    program = _text(_nested(data, "team", "profile", "name")) or _text(_nested(data, "team", "handle"))
    weakness = _text(_nested(data, "weakness", "name"))
    severity = _text(data.get("severity_rating"))
    state = _text(data.get("readable_substate")) or _text(data.get("substate"))

    metadata = [
        ("Source", REPORT_URL_TEMPLATE.format(report_id=report_id)),
        ("Program", program),
        ("Weakness", weakness),
        ("Severity", severity),
        ("State", state),
        ("Disclosed", _text(data.get("disclosed_at"))),
    ]
    lines = [f"# {title}", ""]
    lines += [f"- **{label}:** {value}" for label, value in metadata if value]
    lines += ["", body, ""]
    return "\n".join(lines)


HACKTIVITY_GRAPHQL_URL = "https://hackerone.com/graphql"
HACKTIVITY_INDEX = "CompleteHacktivityReportIndex"
HACKTIVITY_PAGE_SIZE = 100
DEFAULT_DISCOVERY_QUERY = "disclosed:true"
_HACKTIVITY_QUERY = (
    "query($q:String,$first:Int,$after:String){"
    "search(index:" + HACKTIVITY_INDEX + ",query_string:$q,first:$first,after:$after){"
    "pageInfo{hasNextPage endCursor} "
    "edges{node{... on HacktivityDocument{disclosed_at report{databaseId:_id title}}}}}}"
)


def _graphql_post(payload: dict[str, Any], opener: Opener = request.urlopen, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """POST a query to the public HackerOne GraphQL endpoint. No credentials are ever sent."""
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        HACKTIVITY_GRAPHQL_URL,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with opener(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited("HackerOne rate-limited the discovery query (HTTP 429)") from exc
        raise FetchError(f"discovery HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise FetchError(f"could not reach HackerOne GraphQL: {exc.reason}") from exc
    try:
        payload_out = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FetchError("HackerOne GraphQL returned non-JSON (endpoint or schema changed)") from exc
    if payload_out.get("errors"):
        raise FetchError(f"HackerOne GraphQL errors: {json.dumps(payload_out['errors'])[:300]}")
    return payload_out.get("data") or {}


def discover_disclosed_report_ids(
    count: int,
    *,
    query: str = DEFAULT_DISCOVERY_QUERY,
    opener: Opener = request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> list[int]:
    """Discover publicly disclosed report ids from the HackerOne hacktivity feed.

    Pages through the public hacktivity search (no auth, no credentials) and returns
    up to ``count`` disclosed report ids. This is what makes ingestion autonomous: no
    ids need to be supplied by hand.
    """
    if count <= 0:
        return []
    delay = max(delay_seconds, MIN_DELAY_SECONDS)
    ids: list[int] = []
    seen: set[int] = set()
    cursor: str | None = None
    while len(ids) < count:
        variables: dict[str, Any] = {"q": query, "first": min(HACKTIVITY_PAGE_SIZE, count - len(ids))}
        if cursor:
            variables["after"] = cursor
        data = _graphql_post({"query": _HACKTIVITY_QUERY, "variables": variables}, opener=opener, sleep=sleep)
        search = data.get("search") or {}
        edges = search.get("edges") or []
        if not edges:
            break
        for edge in edges:
            report = ((edge or {}).get("node") or {}).get("report") or {}
            try:
                rid = int(report.get("databaseId"))
            except (TypeError, ValueError):
                continue
            if rid not in seen:
                seen.add(rid)
                ids.append(rid)
        page_info = search.get("pageInfo") or {}
        cursor = page_info.get("endCursor")
        if not page_info.get("hasNextPage") or not cursor:
            break
        if len(ids) < count:
            sleep(delay)
    return ids[:count]


def fetch_report_json(
    report_id: int,
    opener: Opener = request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """GET https://hackerone.com/reports/<id>.json. Public reports only; no credentials are ever sent."""
    url = REPORT_URL_TEMPLATE.format(report_id=report_id) + ".json"
    http_request = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    for attempt in range(MAX_RETRIES + 1):
        try:
            with opener(http_request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            if exc.code == 429:
                raise RateLimited("HackerOne returned 429 (too many requests)") from exc
            if exc.code in (401, 403, 404, 410):
                raise ReportUnavailable(f"HTTP {exc.code}") from exc
            if exc.code >= 500 and attempt < MAX_RETRIES:
                sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise FetchError(f"HTTP {exc.code}") from exc
        except error.URLError as exc:
            if attempt < MAX_RETRIES:
                sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise FetchError(f"connection problem: {exc.reason}") from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise FetchError("no response")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportUnavailable("response was not JSON (probably not a public report)") from exc
    if not isinstance(data, dict):
        raise ReportUnavailable("unexpected response shape")
    return data


def fetch_reports(
    report_ids: Iterable[int],
    output_directory: str | Path,
    *,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    max_reports: int = DEFAULT_MAX_REPORTS,
    force: bool = False,
    opener: Opener = request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> FetchSummary:
    """Download disclosed reports as <id>.md files. Stops at once on rate limiting or a repeated block."""
    delay = max(delay_seconds, MIN_DELAY_SECONDS)
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    skipped: list[str] = []
    unavailable: list[str] = []
    failed: list[str] = []
    stopped_reason = ""
    consecutive_blocks = 0
    requests_made = 0

    for report_id in report_ids:
        target = directory / f"{report_id}.md"
        label = str(report_id)

        if target.exists() and not force:
            log(f"[skip] {label}: already downloaded")
            skipped.append(label)
            continue
        if requests_made >= max_reports:
            stopped_reason = f"reached --max {max_reports}"
            break
        if requests_made:
            sleep(delay)
        requests_made += 1

        try:
            data = fetch_report_json(report_id, opener=opener, sleep=sleep)
            markdown = report_to_markdown(report_id, data)
            target.write_text(markdown, encoding="utf-8")
            log(f"[ok]   {label}: {_text(data.get('title'))}")
            saved.append(label)
            consecutive_blocks = 0
        except RateLimited as exc:
            stopped_reason = f"{exc}. Wait a while and run again; already saved files are kept."
            break
        except ReportUnavailable as exc:
            log(f"[n/a]  {label}: {exc}")
            unavailable.append(label)
            consecutive_blocks = consecutive_blocks + 1 if "HTTP 403" in str(exc) else 0
            if consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
                stopped_reason = (
                    f"{MAX_CONSECUTIVE_BLOCKS} HTTP 403 responses in a row: this looks like a block, not private reports. "
                    "Stopping to avoid making it worse."
                )
                break
        except FetchError as exc:
            log(f"[fail] {label}: {exc}")
            failed.append(label)

    return FetchSummary(saved, skipped, unavailable, failed, stopped_reason)
