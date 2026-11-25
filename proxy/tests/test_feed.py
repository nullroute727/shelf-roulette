"""Offline tests for the shelf walk, against a fake HTTP client.

These pin the stop rules: the grid widget's cap is not exact (a 249-book shelf
answered 199 items from a "200" grid), so the walk must trust the profile count
or an order that adds nothing, never a short page.
"""

from __future__ import annotations

import unittest
from typing import Any

from app import feed
from app.config import settings
from app.feed import FeedError, PrivateShelfError, fetch_shelf

GRID_CAP = 200
CUSTOM_CAP = 100


def _js(markup: str) -> bytes:
    escaped = (
        markup.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("/", "\\/")
        .replace("\n", "\\n")
    )
    return (
        f"  var widget_code = '{escaped}'\n"
        "  var widget_div = document.getElementById('gr_grid_widget_1')\n"
    ).encode()


def grid_widget(ids: list[int]) -> bytes:
    items = "".join(
        f'<div class="gr_grid_book_container"><a title="Book {i}" rel="nofollow" '
        f'href="https://www.goodreads.com/review/show/{i}?utm_medium=api"><img alt="Book {i}" '
        f'src="https://i.gr-assets.com/images/S/x/{i}._SX98_.jpg" /></a></div>\n'
        for i in ids
    )
    return _js(f'<div class="gr_grid_container">\n{items}</div>\n')


def custom_widget(ids: list[int]) -> bytes:
    items = "".join(
        f'<div class="gr_custom_each_container_1">\n<div class="gr_custom_book_container_1">'
        f'<a title="Book {i}" rel="nofollow" href="https://www.goodreads.com/review/show/{i}?utm_medium=api">'
        f'<img alt="" src="https://i.gr-assets.com/images/S/x/{i}._SY75_.jpg" /></a></div>\n'
        f'<div class="gr_custom_author_1">by <a href="https://www.goodreads.com/author/show/{i}.A">Author {i}</a></div>\n'
        f"</div>\n"
        for i in ids
    )
    return _js(f'<div class="gr_custom_container_1">\n{items}</div>\n')


NOT_CORRECT = _js("This <a href=\"https://www.goodreads.com/\">goodreads</a> widget is not correct.")


def profile(count: int | None) -> bytes:
    row = f'<a href="/review/list/1?shelf={settings.goodreads_shelf}">x&lrm; ({count})</a>' if count else ""
    return f"<html><body>{row}</body></html>".encode()


class FakeResponse:
    def __init__(self, status: int, content: bytes, url: str) -> None:
        self.status_code = status
        self.content = content
        self.text = content.decode()
        self.url = url


class FakeClient:
    """Answers each (kind, sort, order) once, in whatever order the walk asks."""

    def __init__(
        self,
        shelf: list[int],
        profile_response: FakeResponse | None,
        grid_short_by: int = 0,
        grid_skips: set[int] | None = None,
    ) -> None:
        self.shelf = shelf
        self.profile_response = profile_response
        self.grid_short_by = grid_short_by
        self.grid_skips = grid_skips or set()
        self.calls: list[str] = []

    def _ordered(self, sort: str, order: str) -> list[int]:
        if sort == "date_added":
            ordered = sorted(self.shelf)
        elif sort == "title":
            ordered = sorted(self.shelf, key=lambda i: str(i))
        else:
            ordered = sorted(self.shelf, key=lambda i: (i % 7, i))
        return ordered if order == "a" else ordered[::-1]

    async def get(self, url: str, params: dict[str, str] | None = None) -> FakeResponse:
        if "/user/show/" in url:
            self.calls.append("profile")
            if self.profile_response is None:
                return FakeResponse(200, profile(len(self.shelf)), url)
            return self.profile_response
        params = params or {}
        kind = "grid" if "grid_widget" in url else "custom"
        self.calls.append(f"{kind}:{params['sort']}:{params['order']}")
        ordered = self._ordered(params["sort"], params["order"])
        if kind == "grid":
            shown = [i for i in ordered if i not in self.grid_skips]
            return FakeResponse(200, grid_widget(shown[: GRID_CAP - self.grid_short_by]), url)
        return FakeResponse(200, custom_widget(ordered[:CUSTOM_CAP]), url)


class WalkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._delay = feed.PAGE_DELAY_SECONDS
        feed.PAGE_DELAY_SECONDS = 0
        self._uid = settings.goodreads_user_id
        settings.goodreads_user_id = "1"

    def tearDown(self) -> None:
        feed.PAGE_DELAY_SECONDS = self._delay
        settings.goodreads_user_id = self._uid

    async def test_small_shelf_takes_one_grid_and_one_custom_call(self) -> None:
        client = FakeClient(list(range(1, 51)), None)
        books = await fetch_shelf(client)
        self.assertEqual(len(books), 50)
        self.assertEqual(client.calls, ["profile", "grid:date_added:d", "custom:date_added:d"])
        self.assertTrue(all(b["author"] for b in books))
        self.assertEqual(books[0]["goodreadsUrl"], "https://www.goodreads.com/review/show/50")
        self.assertNotIn("book_id", books[0])

    async def test_short_grid_page_does_not_end_the_walk(self) -> None:
        # The regression: 249 books, first grid answers 199, profile says 249.
        client = FakeClient(list(range(1, 250)), None, grid_short_by=1)
        books = await fetch_shelf(client)
        self.assertEqual(len(books), 249)
        grid_calls = [c for c in client.calls if c.startswith("grid:")]
        self.assertEqual(grid_calls[:2], ["grid:date_added:d", "grid:date_added:a"])
        self.assertLessEqual(len(grid_calls), 3)

    async def test_without_a_count_the_walk_stops_when_nothing_new_arrives(self) -> None:
        signin = FakeResponse(200, b"<title>Sign in</title>", "https://www.goodreads.com/user/sign_in?returnurl=x")
        client = FakeClient(list(range(1, 250)), signin, grid_short_by=1)
        books = await fetch_shelf(client)
        self.assertEqual(len(books), 249)
        grid_calls = [c for c in client.calls if c.startswith("grid:")]
        # d + a cover everything; the next order adds nothing and ends the walk.
        self.assertEqual(len(grid_calls), 3)

    async def test_authors_are_filled_from_several_orders(self) -> None:
        client = FakeClient(list(range(1, 250)), None, grid_short_by=1)
        books = await fetch_shelf(client)
        missing = [b for b in books if not b["author"]]
        custom_calls = [c for c in client.calls if c.startswith("custom:")]
        self.assertGreaterEqual(len(custom_calls), 3)
        self.assertLess(len(missing), 249 - 2 * CUSTOM_CAP)

    async def test_book_the_grid_skips_is_rescued_from_the_custom_widget(self) -> None:
        # Seen live: the grid widget omitted one book from every order, while the
        # custom widget listed it.
        client = FakeClient(list(range(1, 250)), None, grid_short_by=1, grid_skips={7})
        books = await fetch_shelf(client)
        self.assertEqual(len(books), 249)
        rescued = [b for b in books if b["goodreadsUrl"].endswith("/review/show/7")]
        self.assertEqual(len(rescued), 1)
        self.assertEqual(rescued[0]["author"], "Author 7")
        self.assertNotIn("book_id", rescued[0])

    async def test_unknown_user_fails_before_any_widget_call(self) -> None:
        client = FakeClient([1], FakeResponse(404, b"", "https://www.goodreads.com/user/show/1"))
        with self.assertRaisesRegex(PrivateShelfError, "no user with id 1"):
            await fetch_shelf(client)
        self.assertEqual(client.calls, ["profile"])

    async def test_empty_shelf_is_a_private_shelf_error(self) -> None:
        client = FakeClient([], FakeResponse(200, profile(None), "https://www.goodreads.com/user/show/1"))
        with self.assertRaisesRegex(PrivateShelfError, "zero items"):
            await fetch_shelf(client)

    async def test_widget_rejection_is_a_feed_error(self) -> None:
        class Rejecting(FakeClient):
            async def get(self, url: str, params: dict[str, str] | None = None) -> FakeResponse:
                if "widget" in url:
                    return FakeResponse(200, NOT_CORRECT, url)
                return await super().get(url, params)

        with self.assertRaisesRegex(FeedError, "user id is unknown"):
            await fetch_shelf(Rejecting([1, 2], None))


if __name__ == "__main__":
    unittest.main()
