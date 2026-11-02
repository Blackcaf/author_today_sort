import unittest
from unittest.mock import patch

from webapp import (
    _parse_genre_catalog,
    _parse_tag_catalog,
    _suggest_tag_names,
    tag_suggestions,
)


class AutocompleteSourceTests(unittest.TestCase):
    def test_genre_catalog_excludes_navigation_routes(self):
        html = """
        <a href="/work/genre/all/ebook">Все книги</a>
        <a href="/work/genre/fantasy">Фэнтези</a>
        <a href="/work/genre/battle-fantasy">Боевое фэнтези</a>
        <a href="/work/genre/romance">Роман</a>
        """
        genres = _parse_genre_catalog(html)
        self.assertEqual(
            genres,
            [
                {"name": "Боевое фэнтези", "slug": "battle-fantasy"},
                {"name": "Роман", "slug": "romance"},
                {"name": "Фэнтези", "slug": "fantasy"},
            ],
        )

    def test_tag_catalog_preserves_yo_variants_and_removes_exact_duplicates(self):
        payload = {
            "tags": [
                "Тёмное фэнтези",
                "Темное фэнтези",
                " фэнтези ",
                "фэнтези",
                "",
                42,
            ]
        }
        self.assertEqual(
            _parse_tag_catalog(payload),
            ["Тёмное фэнтези", "Темное фэнтези", "фэнтези"],
        )

    def test_local_suggestions_match_substrings_and_prioritize_prefixes(self):
        catalog = [
            "приключения",
            "магия и приключения",
            "темное фэнтези",
            "тёмное фэнтези",
            "фэнтези",
        ]
        self.assertEqual(
            _suggest_tag_names("фэнтези", catalog),
            ["фэнтези", "темное фэнтези", "тёмное фэнтези"],
        )
        self.assertEqual(
            _suggest_tag_names("приключ", catalog),
            ["приключения", "магия и приключения"],
        )

    def test_tag_suggestions_use_local_catalog(self):
        catalog = ["система", "игровая система", "система и уровни"]
        with patch("webapp._load_tag_catalog", return_value=catalog):
            result = tag_suggestions("сист")

        self.assertEqual(
            result,
            {
                "tags": ["система", "игровая система", "система и уровни"],
                "query": "сист",
                "source": "local_catalog",
            },
        )

    def test_tag_suggestions_support_both_yo_and_e_queries(self):
        catalog = ["тёмное фэнтези", "темное фэнтези"]
        with patch("webapp._load_tag_catalog", return_value=catalog):
            result = tag_suggestions("темное")

        self.assertEqual(result["tags"], catalog)


if __name__ == "__main__":
    unittest.main()
