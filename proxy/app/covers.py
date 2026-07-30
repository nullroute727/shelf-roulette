"""Allowlisted cover image proxy with an on-disk cache."""

from __future__ import annotations

import hashlib
import logging
import os
from urllib.parse import urlsplit, urlunsplit

from .config import settings

log = logging.getLogger("shelfroulette.covers")

ALLOWED_DOMAINS = ("goodreads.com", "gr-assets.com")


class CoverUrlError(ValueError):
    """The requested cover URL is not one we are willing to fetch."""


def host_allowed(hostname: str | None) -> bool:
    """Exact-match or true-subdomain check on the parsed hostname.

    A substring test would wrongly accept hosts like goodreads.com.evil.tld, so the
    comparison is anchored: either the host equals an allowed domain, or it ends with
    a dot plus that domain.
    """
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_DOMAINS)


def normalize_url(raw: str) -> str:
    """Validate scheme and host, and return a canonical form used for fetch and cache key."""
    if not raw or not raw.strip():
        raise CoverUrlError("missing url parameter")

    try:
        parts = urlsplit(raw.strip())
    except ValueError as exc:
        raise CoverUrlError(f"url could not be parsed: {exc}") from exc

    if parts.scheme.lower() != "https":
        raise CoverUrlError("only https URLs are allowed")

    if not host_allowed(parts.hostname):
        raise CoverUrlError("host is not an allowed Goodreads image host")

    host = parts.hostname.lower()
    if parts.port:
        host = f"{host}:{parts.port}"

    return urlunsplit(("https", host, parts.path, parts.query, ""))


def cache_key(normalized_url: str) -> str:
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


class CoverCache:
    """Disk cache for cover bytes plus a sidecar file holding the content type."""

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.enabled = False

    def prepare(self) -> None:
        """Create the cache dir and verify it is writable, degrading instead of crashing."""
        try:
            os.makedirs(self.directory, exist_ok=True)
            probe = os.path.join(self.directory, ".write-probe")
            with open(probe, "wb") as handle:
                handle.write(b"ok")
            os.unlink(probe)
        except OSError as exc:
            log.warning(
                "cover cache dir %s is not usable (%s), falling back to pass-through streaming",
                self.directory,
                exc,
            )
            self.enabled = False
            return
        self.enabled = True
        log.info("cover cache ready at %s", self.directory)

    def _paths(self, key: str) -> tuple[str, str]:
        base = os.path.join(self.directory, key)
        return base + ".bin", base + ".type"

    def get(self, key: str) -> tuple[bytes, str] | None:
        if not self.enabled:
            return None
        blob_path, type_path = self._paths(key)
        try:
            with open(blob_path, "rb") as handle:
                data = handle.read()
            with open(type_path, "r", encoding="utf-8") as handle:
                content_type = handle.read().strip()
        except OSError:
            return None
        if not data:
            return None
        return data, content_type or "application/octet-stream"

    def put(self, key: str, data: bytes, content_type: str) -> None:
        if not self.enabled:
            return
        blob_path, type_path = self._paths(key)
        try:
            # Write to temp names then rename so concurrent readers never see a partial file.
            tmp_blob = blob_path + ".tmp"
            tmp_type = type_path + ".tmp"
            with open(tmp_blob, "wb") as handle:
                handle.write(data)
            with open(tmp_type, "w", encoding="utf-8") as handle:
                handle.write(content_type)
            os.replace(tmp_blob, blob_path)
            os.replace(tmp_type, type_path)
        except OSError as exc:
            log.warning("could not cache cover %s: %s", key, exc)


cover_cache = CoverCache(settings.cover_cache_dir)
