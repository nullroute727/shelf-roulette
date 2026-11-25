"""Tests for the on-disk shelf snapshot in main.ShelfStore (needs fastapi, so run in the image)."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

from app import main
from app.config import settings
from app.covers import cover_cache

BOOKS = [{"title": "A", "author": "B", "pages": None, "rating": None, "year": None, "coverUrl": None, "goodreadsUrl": "https://www.goodreads.com/review/show/1"}]


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "last-good.json")
        self._enabled = cover_cache.enabled
        cover_cache.enabled = True
        self._uid, self._shelf, self._ttl = settings.goodreads_user_id, settings.goodreads_shelf, settings.shelf_ttl_seconds
        settings.goodreads_user_id, settings.goodreads_shelf, settings.shelf_ttl_seconds = "1", "to-read", 3600

    def tearDown(self) -> None:
        cover_cache.enabled = self._enabled
        settings.goodreads_user_id, settings.goodreads_shelf, settings.shelf_ttl_seconds = self._uid, self._shelf, self._ttl
        self.dir.cleanup()

    def test_store_writes_a_snapshot_that_a_new_store_restores_as_fresh(self) -> None:
        first = main.ShelfStore(self.path)
        first.store(BOOKS)
        with open(self.path, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["userId"], "1")
        self.assertEqual(payload["shelf"], "to-read")

        second = main.ShelfStore(self.path)
        second.load()
        self.assertEqual(second.last_good, BOOKS)
        self.assertTrue(second.is_fresh())

    def test_old_snapshot_is_stale_only(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"saved_at": time.time() - 7200, "userId": "1", "shelf": "to-read", "books": BOOKS}, handle)
        store = main.ShelfStore(self.path)
        store.load()
        self.assertEqual(store.last_good, BOOKS)
        self.assertFalse(store.is_fresh())
        self.assertIsNone(store.fresh)

    def test_snapshot_for_another_user_or_shelf_is_ignored(self) -> None:
        for user, shelf in (("2", "to-read"), ("1", "read")):
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({"saved_at": time.time(), "userId": user, "shelf": shelf, "books": BOOKS}, handle)
            store = main.ShelfStore(self.path)
            store.load()
            self.assertIsNone(store.last_good, (user, shelf))

    def test_garbage_and_missing_files_are_ignored(self) -> None:
        store = main.ShelfStore(self.path)
        store.load()
        self.assertIsNone(store.last_good)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        store.load()
        self.assertIsNone(store.last_good)

    def test_failure_is_remembered_and_cleared(self) -> None:
        store = main.ShelfStore(self.path)
        store.fail("boom", "detail")
        self.assertTrue(store.recently_failed())
        self.assertEqual(store.error_response().status_code, 502)
        store.store(BOOKS)
        self.assertFalse(store.recently_failed())
        self.assertIsNone(store.last_error)


if __name__ == "__main__":
    unittest.main()
