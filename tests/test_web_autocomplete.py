import unittest
from unittest.mock import patch

from webapp import (
    _load_local_genre_catalog,
    _parse_genre_catalog,
    _parse_genre_payload,
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

    def test_genre_payload_normalizes_and_sorts_local_catalog(self):
        payload = {
            "genres": [
                {"name": "  Фэнтези ", "slug": "fantasy"},
                {"name": "Боевик", "slug": "action"},
                {"name": "", "slug": "broken"},
                "invalid",
            ]
        }
        self.assertEqual(
            _parse_genre_payload(payload),
            [
                {"name": "Боевик", "slug": "action"},
                {"name": "Фэнтези", "slug": "fantasy"},
            ],
        )

    def test_bundled_genre_catalog_contains_all_site_genres(self):
        catalog = _load_local_genre_catalog()
        names = {genre["name"] for genre in catalog}
        self.assertEqual(len(catalog), 71)
        self.assertIn("Фэнтези", names)
        self.assertIn("Попаданцы в магические миры", names)
        self.assertIn("Эротический фанфик", names)

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
                "tags": ["система", "система и уровни", "игровая система"],
                "query": "сист",
                "source": "local_catalog",
            },
        )

    def test_tag_suggestions_support_both_yo_and_e_queries(self):
        catalog = ["тёмное фэнтези", "темное фэнтези"]
        with patch("webapp._load_tag_catalog", return_value=catalog):
            result = tag_suggestions("темное")

        self.assertEqual(result["tags"], catalog)

    def test_genres_endpoint_serves_bundled_catalog(self):
        from fastapi.testclient import TestClient
        from webapp import app

        with TestClient(app) as client:
            response = client.get("/api/genres")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = {genre["name"] for genre in payload["genres"]}
        self.assertEqual(payload["source"], "local_catalog")
        self.assertEqual(len(payload["genres"]), 71)
        self.assertIn("Фэнтези", names)
        self.assertIn("Попаданцы в магические миры", names)

    def test_search_form_accepts_only_finished_flag(self):
        from fastapi.testclient import TestClient
        from webapp import app

        with patch("webapp.threading.Thread") as worker:
            with TestClient(app) as client:
                response = client.post(
                    "/api/search",
                    data={
                        "username": "test_user",
                        "tags": "система",
                        "exclude_library": "false",
                        "only_finished": "false",
                    },
                )

        self.assertEqual(response.status_code, 200)
        job_args = worker.call_args.kwargs.get("args") or worker.call_args.args
        params = job_args[2]
        self.assertFalse(params["only_finished"])
        self.assertFalse(params["exclude_library"])


if __name__ == "__main__":
    unittest.main()
