"""Goodreads shelf fetching, via the public shelf widgets.

The per-shelf RSS feed this used to read was retired by Goodreads in October
2026, and the shelf pages now redirect to sign-in. The embeddable widgets still
answer without a session, so the shelf is assembled from those:

* the public profile page lists each shelf with its count, which tells the walk
  when it is done and is the one place a wrong user id fails loudly;
* the grid widget (about 200 books per call) is the backbone: review id, full
  title and cover for every book, requested in several sort orders and unioned
  until the count is reached or an order adds nothing new;
* the custom widget (100 per call) is the only one that names the author, so it
  is walked the same way just to fill that field in.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import PAGE_DELAY_SECONDS, WIDGET_SORTS, settings
from .widgets import WidgetError, decode_widget, parse_widget, shelf_count

log = logging.getLogger("shelfroulette.feed")


class FeedError(Exception):
    """Upstream shelf could not be fetched or parsed."""


class PrivateShelfError(FeedError):
    """Fetch worked but the shelf is empty, private, or belongs to no such user."""


class _Walker:
    """Sequential upstream requests with the inter-request delay and a fresh widget id each."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.requests = 0

    def _widget_id(self) -> str:
        return f"{int(time.time() * 1000)}{self.requests}"

    async def get(self, label: str, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        if self.requests:
            await asyncio.sleep(PAGE_DELAY_SECONDS)
        self.requests += 1
        try:
            return await self.client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise FeedError(f"request for the {label} failed: {exc}") from exc

    async def widget(self, kind: str, sort: str, order: str) -> list[dict[str, Any]]:
        label = f"{kind} widget ({sort} {order})"
        response = await self.get(
            label,
            settings.widget_url(kind),
            settings.widget_params(kind, sort, order, self._widget_id()),
        )

        # A genuinely private profile answers 401 or 403 rather than an empty widget.
        if response.status_code in (401, 403):
            raise PrivateShelfError(
                f"{label} returned HTTP {response.status_code}, the shelf is not publicly readable"
            )
        if response.status_code != 200:
            raise FeedError(f"{label} returned HTTP {response.status_code}")

        try:
            books = parse_widget(decode_widget(response.content))
        except WidgetError as exc:
            raise FeedError(f"{label}: {exc}") from exc
        log.info("%s yielded %d items", label, len(books))
        return books


async def fetch_shelf(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Assemble the shelf from the widgets and return unique books, newest-added first."""
    walker = _Walker(client)
    expected = await _expected_count(walker)

    # Backbone walk. Every call is capped and the cap is not exact (a 249-book
    # shelf gave 199 from a "200" grid), so the only trustworthy stop signals are
    # reaching the profile's count or an order that adds nothing new.
    books: dict[str, dict[str, Any]] = {}
    complete = False
    for sort, order in WIDGET_SORTS:
        page = await walker.widget("grid", sort, order)
        added = 0
        for book in page:
            if book["book_id"] not in books:
                books[book["book_id"]] = book
                added += 1
        if expected is not None and len(books) >= expected:
            complete = True
            break
        if added == 0:
            complete = expected is None and bool(books)
            break

    # Author pass. Only the custom widget names the author, and it is capped
    # lower, so walk it only as far as there are authors left to fill in. It is
    # also a full record, and the grid has been seen to skip the odd book, so
    # anything it lists that the grid did not is added rather than ignored.
    rescued = 0
    for sort, order in WIDGET_SORTS:
        if books and all(book["author"] for book in books.values()):
            break
        for item in await walker.widget("custom", sort, order):
            book = books.get(item["book_id"])
            if book is None:
                books[item["book_id"]] = item
                rescued += 1
            elif item["author"] and not book["author"]:
                book["author"] = item["author"]
    if rescued:
        log.info("%d books came only from the custom widget", rescued)
    still_missing = sum(1 for book in books.values() if not book["author"])
    if still_missing:
        log.info("%d books have no author from the widgets", still_missing)

    if not books:
        raise PrivateShelfError("the shelf widgets returned zero items")
    if expected is not None and len(books) != expected:
        log.warning(
            "profile says shelf %r holds %d books but the widgets gave %d",
            settings.goodreads_shelf,
            expected,
            len(books),
        )
    elif not complete and expected is None:
        log.warning("shelf may be truncated at %d books after %d grid orders", len(books), len(WIDGET_SORTS))

    log.info("shelf assembled: %d books from %d upstream requests", len(books), walker.requests)
    result = []
    for book in books.values():
        book.pop("book_id", None)
        result.append(book)
    return result


async def _expected_count(walker: _Walker) -> int | None:
    """Shelf size from the public profile, or None when the page will not say.

    A 404 here is the one unambiguous signal that the user id is wrong, so it
    is raised rather than logged. Everything else degrades to "count unknown":
    the widgets are the deliverable, and this page is bigger and more likely to
    change than they are.
    """
    try:
        response = await walker.get("profile page", settings.profile_url)
    except FeedError as exc:
        log.warning("profile count check skipped: %s", exc)
        return None
    if response.status_code == 404:
        raise PrivateShelfError(
            f"Goodreads has no user with id {settings.goodreads_user_id} (profile page is HTTP 404)"
        )
    if response.status_code != 200:
        log.warning("profile page returned HTTP %d, count check skipped", response.status_code)
        return None
    if "/user/sign_in" in str(response.url):
        log.warning("profile page redirected to sign-in; the profile may be private")
        return None
    expected = shelf_count(response.text, settings.goodreads_shelf)
    if expected is None:
        log.info("profile page lists no count for shelf %r", settings.goodreads_shelf)
    else:
        log.info("profile says shelf %r holds %d books", settings.goodreads_shelf, expected)
    return expected
