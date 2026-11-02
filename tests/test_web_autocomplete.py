import unittest
from urllib.parse import parse_qs, urlsplit

from webapp import _parse_genre_catalog, _tag_search_path


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

    def test_tag_search_url_uses_only_supported_query_parameter(self):
        parsed = urlsplit(_tag_search_path("система"))
        self.assertEqual(parsed.path, "/search")
        self.assertEqual(parse_qs(parsed.query), {"q": ["система"]})


if __name__ == "__main__":
    unittest.main()
