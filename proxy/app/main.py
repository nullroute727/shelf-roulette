"""Shelf Roulette backend: re-serves a Goodreads RSS shelf as clean JSON.

The Goodreads API is retired but the per-shelf RSS feed still works. Browsers cannot
read it directly (XML, and CORS-blocked), so this service fetches it server-side,
normalizes it, caches it, and also proxies cover images from the Goodreads CDN.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import (
    BROWSER_USER_AGENT,
    CONNECT_TIMEOUT,
    MAX_COVER_BYTES,
    READ_TIMEOUT,
    settings,
)
from .covers import CoverUrlError, cache_key, cover_cache, normalize_url
from .feed import FeedError, PrivateShelfError, fetch_shelf

# Logging is configured in config.py so that env-parsing warnings are formatted too.
log = logging.getLogger("shelfroulette")

COVER_CACHE_CONTROL = "public, max-age=604800"

PRIVATE_SHELF_DETAIL = (
    "The Goodreads profile or shelf is not publicly readable, so the RSS feed returned "
    "no books. Fix this either by making the profile and shelf public in Goodreads "
    "account settings, or by supplying the private feed key via the GOODREADS_RSS_KEY "
    "environment variable."
)


class ShelfStore:
    """TTL cache plus a separately retained last-known-good result."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.fresh: list[dict[str, Any]] | None = None
        self.fresh_at: float = 0.0
        self.last_good: list[dict[str, Any]] | None = None
        self.last_good_at: float = 0.0
        # Bumped on every successful fetch so waiters on the lock can detect that
        # somebody else already did the work they were about to do.
        self.generation: int = 0

    def is_fresh(self) -> bool:
        return (
            self.fresh is not None
            and (time.monotonic() - self.fresh_at) < settings.shelf_ttl_seconds
        )

    def store(self, books: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        self.fresh = books
        self.fresh_at = now
        self.last_good = books
        self.last_good_at = now
        self.generation += 1


store = ShelfStore()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    cover_cache.prepare()
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT
        ),
        follow_redirects=True,
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    app.state.client = client
    log.info(
        "shelf source: user=%s shelf=%s ttl=%ss key=%s",
        settings.goodreads_user_id,
        settings.goodreads_shelf,
        settings.shelf_ttl_seconds,
        "set" if settings.goodreads_rss_key else "unset",
    )
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(title="Shelf Roulette", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["X-Shelf-Stale", "X-Shelf-Age", "X-Shelf-Cache"],
)


def _error(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/api/shelf")
async def shelf(refresh: str | None = Query(default=None)) -> Response:
    want_refresh = refresh not in (None, "", "0", "false", "no")

    if not want_refresh and store.is_fresh():
        return _shelf_response(store.fresh, stale=False, age=time.monotonic() - store.fresh_at)

    generation_before = store.generation

    async with store.lock:
        # Somebody else fetched while we waited on the lock, so reuse their result.
        if store.generation != generation_before and store.fresh is not None:
            return _shelf_response(
                store.fresh, stale=False, age=time.monotonic() - store.fresh_at
            )
        if not want_refresh and store.is_fresh():
            return _shelf_response(
                store.fresh, stale=False, age=time.monotonic() - store.fresh_at
            )

        try:
            books = await fetch_shelf(app.state.client)
        except PrivateShelfError as exc:
            log.warning("shelf feed returned zero items: %s", exc)
            stale = _stale_response()
            if stale is not None:
                return stale
            return _error(
                502,
                "Shelf is empty or private",
                f"{PRIVATE_SHELF_DETAIL} Upstream signal: {exc}.",
            )
        except FeedError as exc:
            log.warning("shelf fetch failed: %s", exc)
            stale = _stale_response()
            if stale is not None:
                return stale
            return _error(
                502,
                "Could not load the Goodreads shelf",
                f"The Goodreads RSS feed could not be fetched or parsed: {exc}",
            )
        except Exception as exc:  # defensive: never leak a 500 to the frontend
            log.exception("unexpected shelf fetch error")
            stale = _stale_response()
            if stale is not None:
                return stale
            return _error(
                502,
                "Could not load the Goodreads shelf",
                f"Unexpected error while loading the shelf: {exc}",
            )

        store.store(books)
        log.info("shelf refreshed: %d books", len(books))
        return _shelf_response(books, stale=False, age=0.0)


def _stale_response() -> Response | None:
    if store.last_good is None:
        return None
    age = time.monotonic() - store.last_good_at
    log.info("serving stale shelf, age %ds", int(age))
    return _shelf_response(store.last_good, stale=True, age=age)


def _shelf_response(books: list[dict[str, Any]] | None, stale: bool, age: float) -> Response:
    headers = {
        "X-Shelf-Age": str(int(max(age, 0.0))),
        "X-Shelf-Cache": "hit" if age > 0 else "miss",
        "Cache-Control": "no-cache",
    }
    if stale:
        headers["X-Shelf-Stale"] = "1"
    return JSONResponse(content=books or [], headers=headers)


@app.get("/api/cover")
async def cover(url: str = Query(default="")) -> Response:
    try:
        normalized = normalize_url(url)
    except CoverUrlError as exc:
        return _error(400, "Invalid cover URL", str(exc))

    key = cache_key(normalized)
    cached = cover_cache.get(key)
    if cached is not None:
        data, content_type = cached
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": COVER_CACHE_CONTROL, "X-Cover-Cache": "hit"},
        )

    try:
        return await _fetch_cover(app.state.client, normalized, key)
    except Exception as exc:  # defensive: always answer with JSON, never a bare 500
        log.warning("cover fetch failed for %s: %s", normalized, exc)
        return _error(502, "Cover fetch failed", f"Could not fetch the cover image: {exc}")


async def _fetch_cover(client: httpx.AsyncClient, normalized: str, key: str) -> Response:
    request = client.build_request("GET", normalized, headers={"Accept": "image/*,*/*;q=0.8"})
    response = await client.send(request, stream=True)

    if response.status_code != 200:
        await response.aclose()
        return _error(
            502,
            "Cover fetch failed",
            f"Upstream returned HTTP {response.status_code} for the cover image.",
        )

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        await response.aclose()
        return _error(
            502,
            "Cover is not an image",
            f"Upstream returned content-type {content_type or 'unknown'} instead of an image.",
        )

    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_COVER_BYTES:
        await response.aclose()
        return _error(
            502,
            "Cover too large",
            f"The cover image exceeds the {MAX_COVER_BYTES} byte limit.",
        )

    if not cover_cache.enabled:
        return StreamingResponse(
            _passthrough(response),
            media_type=content_type,
            headers={"Cache-Control": COVER_CACHE_CONTROL, "X-Cover-Cache": "disabled"},
        )

    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in response.aiter_bytes(65536):
            total += len(chunk)
            if total > MAX_COVER_BYTES:
                return _error(
                    502,
                    "Cover too large",
                    f"The cover image exceeds the {MAX_COVER_BYTES} byte limit.",
                )
            chunks.append(chunk)
    finally:
        await response.aclose()

    data = b"".join(chunks)
    cover_cache.put(key, data, content_type)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": COVER_CACHE_CONTROL, "X-Cover-Cache": "miss"},
    )


async def _passthrough(response: httpx.Response):
    """Stream upstream bytes straight through when the disk cache is unavailable."""
    total = 0
    try:
        async for chunk in response.aiter_bytes(65536):
            total += len(chunk)
            if total > MAX_COVER_BYTES:
                log.warning("aborting oversized cover stream after %d bytes", total)
                break
            yield chunk
    finally:
        await response.aclose()
