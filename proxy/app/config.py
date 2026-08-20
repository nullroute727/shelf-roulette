"""Environment configuration for Shelf Roulette backend."""

from __future__ import annotations

import logging
import os

_LEVEL = (os.environ.get("LOG_LEVEL") or "info").strip().upper()
logging.basicConfig(
    level=getattr(logging, _LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

log = logging.getLogger("shelfroulette.config")

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Goodreads paginates the shelf feed; the loop stops on the first empty page,
# this cap only exists so a misbehaving upstream cannot spin forever.
MAX_FEED_PAGES = 50

# Delay between sequential page requests so we do not hammer Goodreads.
PAGE_DELAY_SECONDS = 0.25

MAX_COVER_BYTES = 8 * 1024 * 1024

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0

# Ceiling for one whole paginated shelf walk. Without it the worst case is
# MAX_FEED_PAGES * READ_TIMEOUT, which is long enough that nginx gives up first
# and the browser gets an HTML gateway error instead of our JSON one. Kept under
# the 60s proxy_read_timeout in web/nginx.conf so this side always answers first.
SHELF_FETCH_BUDGET_SECONDS = 45.0

# The background refresh runs this many seconds before the cached shelf expires,
# so the cache is replaced rather than allowed to go cold.
REFRESH_MARGIN_SECONDS = 60


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    """Parse an int from env, falling back to the default on anything unparseable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        log.warning("env %s=%r is not an integer, using default %d", name, raw, default)
        return default
    if value < minimum:
        log.warning("env %s=%d is below minimum %d, using default %d", name, value, minimum, default)
        return default
    return value


class Settings:
    def __init__(self) -> None:
        # No default. A public repo must not ship somebody's real Goodreads id, and a
        # wrong id silently spins a stranger's shelf, which is worse than an error.
        self.goodreads_user_id: str = _env_str("GOODREADS_USER_ID", "")
        self.goodreads_shelf: str = _env_str("GOODREADS_SHELF", "to-read")
        self.shelf_ttl_seconds: int = _env_int("SHELF_TTL_SECONDS", 3600, minimum=1)
        # Optional private-feed key. Empty string means "do not send the param".
        self.goodreads_rss_key: str = (os.environ.get("GOODREADS_RSS_KEY") or "").strip()
        self.cover_cache_dir: str = _env_str("COVER_CACHE_DIR", "/data/covers")
        self.port: int = _env_int("PORT", 8000, minimum=1)
        self.log_level: str = _env_str("LOG_LEVEL", "info").lower()
        if not self.goodreads_user_id:
            log.warning(
                "GOODREADS_USER_ID is not set; /api/shelf will return an error explaining "
                "this until it is. Copy .env.example to .env and set it."
            )

    @property
    def feed_url(self) -> str:
        return f"https://www.goodreads.com/review/list_rss/{self.goodreads_user_id}"

    def feed_params(self, page: int) -> dict[str, str]:
        params = {"shelf": self.goodreads_shelf, "page": str(page)}
        if self.goodreads_rss_key:
            params["key"] = self.goodreads_rss_key
        return params


settings = Settings()
