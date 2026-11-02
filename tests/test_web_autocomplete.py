import unittest
from unittest.mock import Mock, patch

from webapp import (
    _parse_genre_catalog,
    _parse_official_tag_suggestions,
    _TAG_SUGGESTION_CACHE,
    _TAG_SUGGESTION_CACHE_LOCK,
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

    def test_official_tag_response_is_parsed_and_deduplicated(self):
        payload = {
            "status": "Success",
            "data": {
                "items": [
                    {"title": "система", "count": 2455},
                    {"title": "игровая система", "count": 190},
                    {"title": "система", "count": 20},
                    {"title": "", "count": 1},
                ],
                "totalCount": 250,
            },
        }
        self.assertEqual(
            _parse_official_tag_suggestions(payload),
            ["система", "игровая система"],
        )

    def test_tag_suggestions_call_official_api_with_query_parameter(self):
        with _TAG_SUGGESTION_CACHE_LOCK:
            _TAG_SUGGESTION_CACHE.clear()
        response = Mock()
        response.json.return_value = {
            "status": "Success",
            "data": {
                "items": [
                    {"title": "система", "count": 2455},
                    {"title": "игровая система", "count": 190},
                ],
                "totalCount": 250,
            },
        }
        with patch("webapp.requests.get", return_value=response) as get:
            result = tag_suggestions("сист")

        self.assertEqual(result["tags"], ["система", "игровая система"])
        self.assertEqual(get.call_args.kwargs["params"], {"query": "сист"})
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"],
            "Bearer guest",
        )
        self.assertEqual(get.call_args.args[0], "https://api.author.today/v2/work/tags")

    def test_tag_suggestions_preserve_standard_fantasy_spelling(self):
        with _TAG_SUGGESTION_CACHE_LOCK:
            _TAG_SUGGESTION_CACHE.clear()
        response = Mock()
        response.json.return_value = {
            "status": "Success",
            "data": {
                "items": [{"title": "фентези", "count": 2159}],
                "totalCount": 109,
            },
        }
        with patch("webapp.requests.get", return_value=response) as get:
            result = tag_suggestions("Фэнтези")

        self.assertEqual(result["tags"], ["фентези"])
        self.assertEqual(get.call_args.kwargs["params"], {"query": "фэнтези"})


if __name__ == "__main__":
    unittest.main()
