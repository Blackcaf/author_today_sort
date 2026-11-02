import unittest

from advanced_search import (
    AuthorTodayClient,
    WorkInfo,
    apply_cycle_rule,
    intersect_groups,
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
