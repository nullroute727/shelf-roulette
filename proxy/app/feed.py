"""Goodreads RSS shelf fetching and parsing."""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any
from xml.etree.ElementTree import ParseError

import httpx

from .config import MAX_FEED_PAGES, PAGE_DELAY_SECONDS, settings

log = logging.getLogger("shelfroulette.feed")


class FeedError(Exception):
    """Upstream feed could not be fetched or parsed."""


class PrivateShelfError(FeedError):
    """Feed parsed fine but contained no items at all."""


def _text(element: ET.Element | None, tag: str) -> str:
    """Return a stripped child text value, or an empty string."""
    if element is None:
        return ""
    raw = element.findtext(tag)
    return raw.strip() if raw else ""


def _to_int(raw: str) -> int | None:
    """Coerce feed text to int, returning None for blanks and junk."""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return int(float(raw))
    except (ValueError, OverflowError):
        return None


def _to_float(raw: str) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw)
    except (ValueError, OverflowError):
        return None
    # Reject NaN and infinities so the JSON stays valid.
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _cover_url(raw: str) -> str | None:
    """Drop the Goodreads placeholder cover so the frontend can use its own fallback."""
    if not raw:
        return None
    if "nophoto" in raw.lower():
        return None
    return raw


def parse_items(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse one RSS page into book dicts. Raises FeedError on unparseable XML."""
    try:
        root = ET.fromstring(xml_bytes)
    except ParseError as exc:
        raise FeedError(f"could not parse RSS XML: {exc}") from exc

    channel = root.find("channel")
    if channel is None:
        raise FeedError("RSS document has no channel element")

    books: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title = _text(item, "title")
        link = _text(item, "link")
        if not title and not link:
            continue

        # num_pages is nested under <book>, not a direct child of <item>.
        pages = _to_int(_text(item.find("book"), "num_pages"))

        books.append(
            {
                "book_id": _text(item, "book_id") or link or title,
                "title": title,
                "author": _text(item, "author_name"),
                "pages": pages,
                "rating": _to_float(_text(item, "average_rating")),
                "year": _to_int(_text(item, "book_published")),
                "coverUrl": _cover_url(_text(item, "book_large_image_url")),
                "goodreadsUrl": link,
            }
        )
    return books


async def fetch_shelf(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Walk the shelf feed page by page until an empty page, then return unique books."""
    seen: set[str] = set()
    books: list[dict[str, Any]] = []
    url = settings.feed_url

    for page in range(1, MAX_FEED_PAGES + 1):
        if page > 1:
            await asyncio.sleep(PAGE_DELAY_SECONDS)

        try:
            response = await client.get(url, params=settings.feed_params(page))
        except httpx.HTTPError as exc:
            raise FeedError(f"request for page {page} failed: {exc}") from exc

        # A genuinely private profile answers 401 or 403 rather than returning an
        # empty feed, so it needs the same "make it public or pass a key" advice.
        if response.status_code in (401, 403):
            raise PrivateShelfError(
                f"page {page} returned HTTP {response.status_code}, the shelf is not publicly readable"
            )

        if response.status_code != 200:
            raise FeedError(f"page {page} returned HTTP {response.status_code}")

        page_books = parse_items(response.content)
        log.info("shelf page %d yielded %d items", page, len(page_books))
        if not page_books:
            break

        for book in page_books:
            key = book.pop("book_id")
            if key in seen:
                continue
            seen.add(key)
            books.append(book)
    else:
        log.warning("hit page cap of %d, shelf may be truncated", MAX_FEED_PAGES)

    if not books:
        raise PrivateShelfError("feed returned zero items")

    return books
