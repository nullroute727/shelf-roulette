"""Offline tests for the Goodreads widget parser.

The fixtures are real widget responses captured in November 2026 with the user
id and name swapped out, plus one hand-added item exercising the placeholder
cover and HTML entities. Run from the proxy directory:

    python3 -m unittest discover -s tests -t .
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.widgets import (
    WidgetError,
    clean_url,
    cover_url,
    decode_widget,
    parse_widget,
    review_id,
    shelf_count,
)

FIXTURES = Path(__file__).parent / "fixtures"

NOT_CORRECT = (
    "  var widget_code = '  This <a rel=\\\"nofollow\\\" href=\\\"https://www.goodreads.com/\\\">"
    "goodreads<\\/a>\\n  widget is not correct. Get the corrected widget code\\n'\n"
    "  var widget_div = document.getElementById('gr_grid_widget_1')\n"
)

PROFILE_SNIPPET = """
<a href="https://www.goodreads.com/review/list/12345678-someone?shelf=currently-reading">x</a>
<div class="userShelf">
  <a href="/review/list/12345678?shelf=to-read">               to-read&lrm;               (1,249) </a>
  <a href="/review/list/12345678?shelf=to-read-later">         to-read-later&lrm;         (3) </a>
  <a href="/review/list/12345678?shelf=read">                  read&lrm;                  (150) </a>
</div>
"""


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class DecodeTests(unittest.TestCase):
    def test_grid_decodes_to_markup(self) -> None:
        markup = decode_widget(load("grid_widget.js"))
        self.assertIn('<div class="gr_grid_container">', markup)
        self.assertIn("</a></div>", markup)
        self.assertNotIn("\\n", markup)
        self.assertIn("Someone's favorite books", markup)

    def test_not_a_widget(self) -> None:
        with self.assertRaises(WidgetError):
            decode_widget(b"<!DOCTYPE html><html><title>Page not found</title></html>")

    def test_unknown_user_is_rejected(self) -> None:
        with self.assertRaisesRegex(WidgetError, "user id"):
            decode_widget(NOT_CORRECT)


class GridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.books = parse_widget(decode_widget(load("grid_widget.js")))

    def test_every_item_is_found(self) -> None:
        self.assertEqual([b["book_id"] for b in self.books], ["9113593287", "9101986200", "8000000001"])

    def test_full_title_comes_from_the_title_attribute(self) -> None:
        self.assertEqual(self.books[0]["title"], "Web of Vows and Vengeance (The Hirathean Path, #1)")

    def test_link_loses_tracking_query(self) -> None:
        self.assertEqual(self.books[0]["goodreadsUrl"], "https://www.goodreads.com/review/show/9113593287")

    def test_cover_is_kept(self) -> None:
        self.assertEqual(
            self.books[1]["coverUrl"],
            "https://i.gr-assets.com/images/S/compressed.photo.goodreads.com/books/1792582119l/255014432._SX98_.jpg",
        )

    def test_placeholder_cover_is_dropped_and_entities_unescaped(self) -> None:
        self.assertIsNone(self.books[2]["coverUrl"])
        self.assertEqual(self.books[2]["title"], "Salt & Iron: O'Brien's Tale")

    def test_grid_has_no_author_or_metadata(self) -> None:
        for book in self.books:
            self.assertEqual(book["author"], "")
            self.assertIsNone(book["pages"])
            self.assertIsNone(book["rating"])
            self.assertIsNone(book["year"])


class CustomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.books = parse_widget(decode_widget(load("custom_widget.js")))

    def test_items_and_authors(self) -> None:
        self.assertEqual(len(self.books), 2)
        self.assertEqual(self.books[0]["author"], "Aria Ashbrook")
        self.assertEqual(self.books[1]["author"], "Rachel Gillig")

    def test_star_sprites_are_not_mistaken_for_covers(self) -> None:
        self.assertTrue(self.books[0]["coverUrl"].endswith("230935823._SY75_.jpg"))

    def test_same_ids_as_grid(self) -> None:
        grid = parse_widget(decode_widget(load("grid_widget.js")))
        self.assertEqual([b["book_id"] for b in self.books], [b["book_id"] for b in grid[:2]])


class HelperTests(unittest.TestCase):
    def test_review_id(self) -> None:
        self.assertEqual(review_id("https://www.goodreads.com/review/show/42?x=1"), "42")
        self.assertIsNone(review_id("https://www.goodreads.com/author/show/42.Name"))
        self.assertIsNone(review_id(""))

    def test_clean_url(self) -> None:
        self.assertEqual(clean_url(" https://a.b/c/d?e=f#g "), "https://a.b/c/d")

    def test_cover_url(self) -> None:
        self.assertIsNone(cover_url(None))
        self.assertIsNone(cover_url("https://s.gr-assets.com/assets/NoPhoto/book/x.png"))
        self.assertEqual(cover_url("https://i.gr-assets.com/x.jpg"), "https://i.gr-assets.com/x.jpg")

    def test_shelf_count_matches_the_exact_shelf(self) -> None:
        self.assertEqual(shelf_count(PROFILE_SNIPPET, "to-read"), 1249)
        self.assertEqual(shelf_count(PROFILE_SNIPPET, "to-read-later"), 3)
        self.assertEqual(shelf_count(PROFILE_SNIPPET, "read"), 150)
        self.assertIsNone(shelf_count(PROFILE_SNIPPET, "did-not-finish"))
        self.assertIsNone(shelf_count("<title>Sign in</title>", "to-read"))


if __name__ == "__main__":
    unittest.main()
