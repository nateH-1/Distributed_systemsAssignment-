"""Deterministic mailbox placement shared by every front end."""

from __future__ import annotations

import hashlib

from config import Site


def home_site(user: str, sites: tuple[Site, ...]) -> Site:
    """Choose a site with a stable SHA-256 hash, never Python's randomized hash()."""
    if not user:
        raise ValueError("user must not be empty")
    digest = hashlib.sha256(user.encode("utf-8")).digest()
    return sites[int.from_bytes(digest, "big") % len(sites)]
