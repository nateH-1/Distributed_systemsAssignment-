"""Configuration loading for a site's node-1 front end.

The same ``MAIL_SITES`` value must be installed at every site, in the same
order.  Keeping the value in an environment variable makes deployment on the
course nodes explicit instead of embedding one student's address in code.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os


@dataclass(frozen=True)
class Site:
    name: str
    host: str


@dataclass(frozen=True)
class Settings:
    site_name: str
    sites: tuple[Site, ...]
    storage_nodes: tuple[str, ...]
    storage_port: int = 9000
    peer_port: int = 8000
    timeout_seconds: float = 3.0

    @property
    def local_site(self) -> Site:
        for site in self.sites:
            if site.name == self.site_name:
                return site
        raise ValueError(f"MAIL_SITE_NAME {self.site_name!r} is not in MAIL_SITES")


def load_settings() -> Settings:
    """Load settings from environment variables with local-development defaults."""
    raw_sites = os.environ.get(
        "MAIL_SITES", '[{"name": "site-a", "host": "127.0.0.1"}]'
    )
    try:
        parsed_sites = json.loads(raw_sites)
        sites = tuple(Site(name=item["name"], host=item["host"]) for item in parsed_sites)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("MAIL_SITES must be JSON like [{\"name\": \"site-a\", \"host\": \"10.0.0.5\"}]") from exc

    if not sites or len({site.name for site in sites}) != len(sites):
        raise ValueError("MAIL_SITES must contain one or more uniquely named sites")

    storage_nodes = tuple(
        host.strip()
        for host in os.environ.get("MAIL_STORAGE_NODES", "node-2,node-3").split(",")
        if host.strip()
    )
    if not storage_nodes:
        raise ValueError("MAIL_STORAGE_NODES must name at least one storage node")

    return Settings(
        site_name=os.environ.get("MAIL_SITE_NAME", sites[0].name),
        sites=sites,
        storage_nodes=storage_nodes,
        storage_port=int(os.environ.get("MAIL_STORAGE_PORT", "9000")),
        peer_port=int(os.environ.get("MAIL_PORT", "8000")),
        timeout_seconds=float(os.environ.get("MAIL_TIMEOUT_SECONDS", "3")),
    )
