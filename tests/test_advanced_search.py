import unittest
from urllib.parse import parse_qs, urlsplit

from advanced_search import (
    AuthorTodayClient,
    WorkInfo,
    apply_cycle_rule,
    intersect_groups,
    load_local_genres,
    parse_library_ids,
    split_groups,
)


class SearchLogicTests(unittest.TestCase):
    def test_groups_use_or_inside_and_between_lines(self):
        self.assertEqual(
            split_groups("гарем, гаремник\nсистема\n\nпопаданцы"),
            [["гарем", "гаремник"], ["система"], ["попаданцы"]],
        )
        self.assertEqual(
            intersect_groups([{"1", "2", "3"}, {"2", "3"}, {"3", "4"}]),
            {"3"},
        )

    def test_library_parser_extracts_ids(self):
        html = (
            '<div class="bookcard" id="work-123"></div>'
            '<div class="bookcard" id="work-456"></div>'
        )
        self.assertEqual(parse_library_ids(html), {"123", "456"})

    def test_search_url_uses_valid_work_search_sorting(self):
        url = AuthorTodayClient._search_url(["фэнтези", "магия"], 3, only_finished=True)
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)

        self.assertEqual(query["category"], ["works"])
        self.assertEqual(query["view"], ["list"])
        self.assertEqual(query["sorting"], ["popular"])
        self.assertEqual(query["finished"], ["true"])
        self.assertEqual(query["tags"], ["фэнтези", "магия"])
        self.assertEqual(query["page"], ["3"])

    def test_search_url_omits_finished_filter_when_disabled(self):
        url = AuthorTodayClient._search_url(["фэнтези"], 1, only_finished=False)
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)

        self.assertNotIn("finished", query)

    def test_genre_url_uses_finished_state_and_pagination(self):
        url = AuthorTodayClient._genre_url("fantasy", 2, only_finished=True)
        query = parse_qs(urlsplit(url).query)

        self.assertEqual(urlsplit(url).path, "/work/genre/fantasy")
        self.assertEqual(query["state"], ["finished"])
        self.assertEqual(query["page"], ["2"])

    def test_genre_url_omits_finished_state_when_disabled(self):
        url = AuthorTodayClient._genre_url("fantasy", 1, only_finished=False)
        query = parse_qs(urlsplit(url).query)

        self.assertEqual(urlsplit(url).path, "/work/genre/fantasy")
        self.assertNotIn("state", query)

    def test_local_genre_catalog_is_bundled(self):
        catalog = load_local_genres()
        self.assertGreaterEqual(len(catalog), 70)
        self.assertEqual(catalog["фэнтези"], "fantasy")
        self.assertEqual(catalog["попаданцы в магические миры"], "popadantsy-v-magicheskie-miry")

    def test_cycle_rule_keeps_only_matching_first_part(self):
        works = [
            WorkInfo(id="101", title="Part 1", series_id="10", series_name="Saga", series_number=1),
            WorkInfo(id="102", title="Part 2", series_id="10", series_name="Saga", series_number=2),
            WorkInfo(id="103", title="Standalone"),
        ]
        result = apply_cycle_rule(works)
        self.assertEqual({work.id for work in result}, {"101", "103"})

    def test_cycle_rule_keeps_all_matching_parts_when_first_does_not_match(self):
        works = [
            WorkInfo(id="102", title="Part 2", series_id="10", series_name="Saga", series_number=2),
            WorkInfo(id="103", title="Part 3", series_id="10", series_name="Saga", series_number=3),
        ]
        result = apply_cycle_rule(works)
        self.assertEqual({work.id for work in result}, {"102", "103"})

    def test_work_page_parser_finds_tags_after_annotation(self):
        page = """
        <h1>Part One</h1>
        <a href="#annotation">Аннотация</a>
        <div id="annotation">A sufficiently long annotation about a hero and magic.</div>
        <footer><a href="/work/tag/sistema">система</a></footer>
        """
        info = AuthorTodayClient.parse_work_detail("556", "work", page)
        self.assertEqual(info.tags, ["система"])

    def test_work_page_parser_reads_cycle_genres_tags_and_annotation(self):
        page = """
        <h1>Part One</h1>
        <a href="/u/author/works">Author</a>
        <a href="/work/genre/fantasy">Фэнтези</a>
        <span>Цикл: <a href="/work/series/777">Saga</a> #1</span>
        <a href="/work/tag/gar">гарем</a>
        <a href="#annotation">Аннотация</a>
        <div id="annotation">A sufficiently long annotation about a hero and magic.</div>
        """
        info = AuthorTodayClient.parse_work_detail("555", "work", page)
        self.assertEqual(info.title, "Part One")
        self.assertEqual(info.author, "Author")
        self.assertEqual(info.genres, ["Фэнтези"])
        self.assertEqual(info.tags, ["гарем"])
        self.assertEqual(info.series_id, "777")
        self.assertEqual(info.series_number, 1)
        self.assertIn("hero", info.description)


if __name__ == "__main__":
    unittest.main()
