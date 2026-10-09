"""Focused tests for local reference-family retrieval filters."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_search import ReferenceSearch


class FakeEncoder:
    def encode(self, _texts):
        return np.asarray([[1.0, 0.0]], dtype=np.float32)


class FakeReranker:
    def score(self, _query, passages):
        return [0.0] * len(passages)


def synthetic_reference_search() -> ReferenceSearch:
    search = ReferenceSearch.__new__(ReferenceSearch)
    search.encoder = FakeEncoder()
    search.reranker = FakeReranker()
    search.entries = {
        "First Aid": {"id": "First Aid", "title": "First Aid", "printedPageOffset": 0},
        "Pathoma": {"id": "Pathoma", "title": "Pathoma", "printedPageOffset": 0},
    }
    search.records = [
        {"documentId": "First Aid", "physicalPage": 12,
         "excerpt": "Interferon beta treatment for multiple sclerosis"},
        {"documentId": "Pathoma", "physicalPage": 19,
         "excerpt": "Multiple sclerosis is treated with interferon beta"},
    ]
    search.vectors = np.asarray([[1.0, 0.0], [0.9, 0.1]], dtype=np.float32)
    search._get_page_layer = lambda _book, _page: {"spans": []}
    search._focus_indices = lambda _layer, _query: []
    search._printed_page = lambda _book, page, _layer: page
    search._page_url = lambda book, page: f"http://127.0.0.1/source/{book}#page={page}"
    search.source_index = {
        "documentCount": 3,
        "documentFrequency": {},
        "records": [
            {"documentId": "mehlman-neuro", "physicalPage": 2,
             "excerpt": "Multiple sclerosis interferon beta study guide"},
            {"documentId": "school-notes", "physicalPage": 5,
             "excerpt": "Multiple sclerosis interferon beta lecture notes"},
        ],
    }
    search.documents = {
        "mehlman-neuro": {"title": "Mehlman HY Neuro"},
        "school-notes": {"title": "Neurology lecture notes"},
    }
    search._page_url = lambda document_id, page: f"http://127.0.0.1/source/{document_id}#page={page}"
    return search


class ReferenceFamilySearchTests(unittest.TestCase):
    def test_book_family_filters_return_only_enabled_book(self):
        search = synthetic_reference_search()
        query = "multiple sclerosis interferon beta"

        default = search.search_books(query)
        first_aid = search.search_books(query, families={"first_aid"})
        pathoma = search.search_books(query, families={"pathoma"})
        empty = search.search_books(query, families=set())
        invalid = search.search_books(query, families={"all"})

        self.assertTrue(default["First Aid"])
        self.assertTrue(default["Pathoma"])
        self.assertTrue(first_aid["First Aid"])
        self.assertEqual(first_aid["Pathoma"], [])
        self.assertTrue(pathoma["Pathoma"])
        self.assertEqual(pathoma["First Aid"], [])
        self.assertEqual(empty, {"First Aid": [], "Pathoma": []})
        self.assertEqual(invalid, {"First Aid": [], "Pathoma": []})

    def test_source_family_filters_separate_mehlman_and_in_house(self):
        search = synthetic_reference_search()
        query = "multiple sclerosis interferon beta"

        default = search.search_sources(query)
        mehlman = search.search_sources(query, families={"mehlman"})
        in_house = search.search_sources(query, families={"in_house"})
        empty = search.search_sources(query, families=set())
        invalid = search.search_sources(query, families={"all"})

        self.assertEqual({row["documentId"] for row in default}, {"mehlman-neuro", "school-notes"})
        self.assertEqual([row["documentId"] for row in mehlman], ["mehlman-neuro"])
        self.assertEqual([row["documentId"] for row in in_house], ["school-notes"])
        self.assertEqual(empty, [])
        self.assertEqual(invalid, [])


if __name__ == "__main__":
    unittest.main()
