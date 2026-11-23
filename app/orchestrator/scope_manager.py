"""Local scope management — own your scope in hackbot, independent of HackerOne.

You seed a program with a host (e.g. hackycorp.com). As recon discovers more hosts,
this proposes the new ones; you accept (→ in_scope) or reject (→ out_of_scope block
list). The file is written in the HackerOne scope format the recon modules and the
auto-exploit engine already understand (asset_type / asset_identifier), so nothing
downstream has to change — but the scope no longer has to come from a bug-bounty
platform.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_HACKBOT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECON_REPO = _HACKBOT_ROOT / "external" / "auto-bug-bounty"


def _norm(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def apex_of(host: str) -> str:
    """Best-effort registrable domain: keep 3 labels for 2-level ccTLDs (com.br, co.uk),
    else 2. hackycorp.com -> hackycorp.com ; a.b.businesscorp.com.br -> businesscorp.com.br."""
    host = _norm(host).lstrip("*.")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    two_level = {"com", "co", "net", "org", "gov", "edu", "ac"}
    if len(labels) >= 3 and labels[-2] in two_level and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass
class ScopeManager:
    program: str
    platform: str
    path: Path
    data: dict

    @classmethod
    def load_or_create(cls, program: str, platform: str, scopes_dir: Path | str | None = None) -> "ScopeManager":
        base = Path(scopes_dir) if scopes_dir else DEFAULT_RECON_REPO / "scopes"
        path = base / platform / f"{program}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = {
                "program": program,
                "platform": platform,
                "offers_bounty": False,
                "url": "",
                "in_scope": [],
                "out_of_scope": [],
            }
        data.setdefault("in_scope", [])
        data.setdefault("out_of_scope", [])
        return cls(program, platform, path, data)

    # -- queries -------------------------------------------------------------
    @staticmethod
    def _matches(host: str, assets: list[dict]) -> bool:
        host = _norm(host)
        for entry in assets or []:
            ident = _norm(entry.get("asset_identifier", ""))
            if not ident:
                continue
            if (entry.get("asset_type", "").upper() == "WILDCARD") or ident.startswith("*."):
                base = ident.lstrip("*.")
                if host == base or host.endswith("." + base):
                    return True
            elif host == ident:
                return True
        return False

    def is_in_scope(self, host: str) -> bool:
        return self._matches(host, self.data["in_scope"])

    def is_blocked(self, host: str) -> bool:
        return self._matches(host, self.data["out_of_scope"])

    def is_known(self, host: str) -> bool:
        return self.is_in_scope(host) or self.is_blocked(host)

    def propose_new(self, hosts) -> list[str]:
        """Discovered hosts not yet accepted or blocked — the ones worth asking about."""
        out: list[str] = []
        for host in dict.fromkeys(_norm(h) for h in hosts):
            if host and not self.is_known(host):
                out.append(host)
        return out

    # -- mutations -----------------------------------------------------------
    def _drop(self, key: str, host: str) -> None:
        host = _norm(host)
        self.data[key] = [e for e in self.data[key] if _norm(e.get("asset_identifier", "")) != host]

    def add_host(self, host: str, wildcard: bool = False) -> None:
        host = _norm(host)
        if not host:
            return
        self._drop("out_of_scope", host)  # accepting overrides a previous block
        present = {_norm(e.get("asset_identifier", "")) for e in self.data["in_scope"]}
        if host not in present:
            self.data["in_scope"].append({"asset_type": "DOMAIN", "asset_identifier": host})
        if wildcard:
            wc = f"*.{host}"
            if _norm(wc) not in present:
                self.data["in_scope"].append({"asset_type": "WILDCARD", "asset_identifier": wc})

    def block_host(self, host: str) -> None:
        host = _norm(host)
        if not host:
            return
        self._drop("in_scope", host)
        present = {_norm(e.get("asset_identifier", "")) for e in self.data["out_of_scope"]}
        if host not in present:
            self.data["out_of_scope"].append({"asset_type": "DOMAIN", "asset_identifier": host})

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return self.path
