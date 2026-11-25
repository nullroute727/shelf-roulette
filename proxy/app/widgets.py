"""Parsing for the Goodreads public shelf widgets.

Goodreads retired the per-shelf RSS feeds in October 2026. What still works
without signing in is the pair of embeddable shelf widgets that the site offers
under "Share your books":

    /review/grid_widget/<user-id>.<any>?shelf=...&num_books=...&sort=...&order=...
    /review/custom_widget/<user-id>.<any>?shelf=...&show_author=1&...

Both answer with a small JavaScript file holding the widget markup in a
single-quoted string. This module turns that script into book dicts. It is pure
standard library and does no I/O, so it is unit-tested offline.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# The whole widget lives in one JS string literal.
_CODE_RE = re.compile(r"var widget_code = '(.*?)'\s*\n\s*var widget_div", re.S)
_ESCAPE_RE = re.compile(r"\\(.)", re.S)
_REVIEW_ID_RE = re.compile(r"/review/show/(\d+)")
_AUTHOR_HREF_RE = re.compile(r"/author/show/")

# Goodreads' own wording when the user id in the widget URL does not exist.
NOT_CORRECT_MARKER = "widget is not correct"


class WidgetError(ValueError):
    """The response is not a widget script, or Goodreads rejected the widget."""


def _unescape_js(match: re.Match[str]) -> str:
    char = match.group(1)
    return {"n": "\n", "t": "\t", "r": "\r"}.get(char, char)


def decode_widget(raw: bytes | str) -> str:
    """Return the HTML inside a widget script, with the JS string escapes undone."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    match = _CODE_RE.search(text)
    if match is None:
        raise WidgetError("response is not a Goodreads widget script")
    markup = _ESCAPE_RE.sub(_unescape_js, match.group(1))
    if NOT_CORRECT_MARKER in markup:
        raise WidgetError("Goodreads says the widget is not correct, which means the user id is unknown")
    return markup


def review_id(href: str) -> str | None:
    match = _REVIEW_ID_RE.search(href or "")
    return match.group(1) if match else None


def clean_url(href: str) -> str:
    """Drop the widget's utm_* query so the link matches what the RSS feed used to give."""
    parts = urlsplit(href.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def cover_url(raw: str | None) -> str | None:
    """Drop the Goodreads placeholder cover so the frontend can use its own fallback."""
    if not raw:
        return None
    if "nophoto" in raw.lower():
        return None
    return raw


class _WidgetParser(HTMLParser):
    """Collects one dict per book container in either widget flavour.

    Grid items are a single `gr_grid_book_container` div. Custom items are a
    `gr_custom_each_container_<id>` div holding cover, title and author blocks.
    Both put the full title (with series) in the cover link's title attribute.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, Any]] = []
        self._item: dict[str, Any] | None = None
        self._depth = 0  # div nesting inside the current item
        self._capture: str | None = None  # "title" or "author" while inside that <a>
        self._buffer: list[str] = []

    @staticmethod
    def _is_item_start(classes: str) -> bool:
        return any(
            cls == "gr_grid_book_container" or cls.startswith("gr_custom_each_container")
            for cls in classes.split()
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}
        if tag == "div":
            if self._item is None:
                if self._is_item_start(attr.get("class", "")):
                    self._item = {"title": "", "author": "", "cover": None, "link": ""}
                    self._depth = 1
            else:
                self._depth += 1
            return

        if self._item is None:
            return

        if tag == "a":
            href = attr.get("href", "")
            if review_id(href):
                if not self._item["link"]:
                    self._item["link"] = href
                if attr.get("title") and not self._item["title"]:
                    self._item["title"] = attr["title"].strip()
                elif not self._item["title"]:
                    self._capture = "title"
                    self._buffer = []
            elif _AUTHOR_HREF_RE.search(href) and not self._item["author"]:
                self._capture = "author"
                self._buffer = []
        elif tag == "img" and self._item["cover"] is None:
            src = attr.get("src", "")
            # Skip the star-rating sprites and the branding logo.
            if src and "/images/layout/" not in src and "widget_logo" not in src:
                self._item["cover"] = src

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._item is None:
            return
        if tag == "a" and self._capture:
            text = " ".join("".join(self._buffer).split())
            if text and not self._item[self._capture]:
                self._item[self._capture] = text
            self._capture = None
            self._buffer = []
        elif tag == "div":
            self._depth -= 1
            if self._depth <= 0:
                self.items.append(self._item)
                self._item = None


def parse_widget(markup: str) -> list[dict[str, Any]]:
    """Parse decoded widget HTML into book dicts keyed on the Goodreads review id.

    Books without a review link are dropped: the id is what the shelf walk unions
    on, and nothing useful can be done with an item that has none.
    """
    parser = _WidgetParser()
    parser.feed(markup)
    parser.close()

    books: list[dict[str, Any]] = []
    for item in parser.items:
        key = review_id(item["link"])
        if not key:
            continue
        books.append(
            {
                "book_id": key,
                "title": html.unescape(item["title"]),
                "author": html.unescape(item["author"]),
                # The widgets carry none of these. The slip simply omits them.
                "pages": None,
                "rating": None,
                "year": None,
                "coverUrl": cover_url(item["cover"]),
                "goodreadsUrl": clean_url(item["link"]),
            }
        )
    return books


def shelf_count(profile_html: str, shelf: str) -> int | None:
    """Read `(249)` next to the shelf link on a public profile page, or None."""
    pattern = re.compile(r"shelf=" + re.escape(shelf) + r'"[^<]*?\(([\d,]+)\)')
    match = pattern.search(profile_html)
    if match is None:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None
