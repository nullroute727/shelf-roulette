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

# Hard caps Goodreads applies to num_books on each widget flavour. Asking for
# more is silently clamped, and page/per_page are ignored, so a shelf larger than
# a cap is covered by requesting the same shelf in several sort orders and
# unioning the results.
GRID_WIDGET_CAP = 200
CUSTOM_WIDGET_CAP = 100

# Sort orders walked, in this order, until the union stops growing. Two orders of
# one key cover a shelf of up to twice the cap deterministically; the further keys
# are for shelves bigger than that.
WIDGET_SORTS = (
    ("date_added", "d"),
    ("date_added", "a"),
    ("title", "a"),
    ("title", "d"),
    ("author", "a"),
    ("author", "d"),
)

# Delay between sequential upstream requests so we do not hammer Goodreads.
PAGE_DELAY_SECONDS = 0.25

MAX_COVER_BYTES = 8 * 1024 * 1024

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0

# Ceiling for one whole shelf walk. Without it the worst case is every request
# hitting READ_TIMEOUT, which is long enough that nginx gives up first and the
# browser gets an HTML gateway error instead of our JSON one. Kept under the 60s
# proxy_read_timeout in web/nginx.conf so this side always answers first.
SHELF_FETCH_BUDGET_SECONDS = 45.0

# The background refresh runs this many seconds before the cached shelf expires,
# so the cache is replaced rather than allowed to go cold.
REFRESH_MARGIN_SECONDS = 60

# After a failed fetch, requests inside this window are answered from the last
# good copy (or the remembered error) without another upstream round trip.
# Otherwise every page load, and each of its retries, walks Goodreads again
# while it is down.
FAILURE_BACKOFF_SECONDS = 60

# The last successfully parsed shelf is written here, inside the cover cache
# volume, so a restart during a Goodreads outage still has something to serve.
LAST_GOOD_FILENAME = "last-good.json"


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
        self.cover_cache_dir: str = _env_str("COVER_CACHE_DIR", "/data/covers")
        self.port: int = _env_int("PORT", 8000, minimum=1)
        self.log_level: str = _env_str("LOG_LEVEL", "info").lower()
        if not self.goodreads_user_id:
            log.warning(
                "GOODREADS_USER_ID is not set; /api/shelf will return an error explaining "
                "this until it is. Copy .env.example to .env and set it."
            )
        if os.environ.get("GOODREADS_RSS_KEY", "").strip():
            log.warning(
                "GOODREADS_RSS_KEY is set but Goodreads retired the RSS feeds in October "
                "2026; the key is ignored and the shelf must be publicly visible."
            )

    @property
    def profile_url(self) -> str:
        return f"https://www.goodreads.com/user/show/{self.goodreads_user_id}"

    @property
    def last_good_path(self) -> str:
        return os.path.join(self.cover_cache_dir, LAST_GOOD_FILENAME)

    def widget_url(self, kind: str) -> str:
        # The segment after the dot is the widget's display title; Goodreads
        # ignores it for lookup, so a fixed token keeps the URL stable.
        return f"https://www.goodreads.com/review/{kind}_widget/{self.goodreads_user_id}.shelf"

    def widget_params(
        self, kind: str, sort: str, order: str, widget_id: str
    ) -> dict[str, str]:
        params = {
            "shelf": self.goodreads_shelf,
            "sort": sort,
            "order": order,
            # Goodreads caches a widget by this id and ignores the other params on
            # a repeat, so the caller passes a fresh one every request.
            "widget_id": widget_id,
        }
        if kind == "grid":
            params["num_books"] = str(GRID_WIDGET_CAP)
            params["cover_size"] = "medium"
        else:
            params["num_books"] = str(CUSTOM_WIDGET_CAP)
            params.update(
                show_author="1",
                show_title="1",
                show_cover="1",
                show_rating="0",
                show_review="0",
                show_tags="0",
            )
        return params


settings = Settings()
