#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Advanced Author.Today search with tag/genre groups, description matching,
personal-library exclusions, and cycle-aware result reduction.

The original strict_tag_search.py is intentionally left untouched.
"""
from __future__ import annotations

import csv
import json
import queue
import re
import sqlite3
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://author.today"
CACHE_PATH = Path(__file__).with_name("author_today_cache.sqlite3")
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def split_groups(value: str) -> list[list[str]]:
    """Each non-empty line is AND; comma-separated values within a line are OR."""
    groups: list[list[str]] = []
    for line in value.splitlines():
        alternatives = [part.strip() for part in line.split(",") if part.strip()]
        if alternatives:
            groups.append(alternatives)
    return groups


def intersect_groups(groups: list[set[str]]) -> set[str]:
    if not groups:
        return set()
    result = set(groups[0])
    for group in groups[1:]:
        result.intersection_update(group)
    return result


def extract_work_id(href: str) -> tuple[str, str] | None:
    match = re.search(r"/(work|audiobook)/(\d+)", href or "")
    if not match:
        return None
    return match.group(2), match.group(1)


def page_has_next(soup: BeautifulSoup, current_page: int) -> bool:
    rel_next = soup.select_one('a[rel="next"]')
    if rel_next and rel_next.get("href"):
        return True
    for link in soup.select(".pagination a[href], a[href*='page=']"):
        match = re.search(r"[?&]page=(\d+)", link.get("href", ""))
        if match and int(match.group(1)) > current_page:
            return True
    return False


def parse_result_cards(page_html: str) -> dict[str, dict[str, str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    cards = soup.select(".book-row, .bookcard")
    found: dict[str, dict[str, str]] = {}

    for card in cards:
        anchor = card.select_one('a[href*="/work/"], a[href*="/audiobook/"]')
        if not anchor:
            continue
        parsed = extract_work_id(anchor.get("href", ""))
        if not parsed:
            continue
        work_id, kind = parsed
        title_node = card.select_one(".book-title a, .bookcard-title a, h4.bookcard-title a")
        title = title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True)
        author_node = card.select_one(".book-authors a, .bookcard-authors a, h5.bookcard-authors a")
        author = author_node.get_text(" ", strip=True) if author_node else ""
        found.setdefault(work_id, {"id": work_id, "kind": kind, "title": title, "author": author})

    # Fallback for a layout change: extract work links from the page.
    if not found:
        for anchor in soup.select('a[href*="/work/"], a[href*="/audiobook/"]'):
            parsed = extract_work_id(anchor.get("href", ""))
            if not parsed:
                continue
            work_id, kind = parsed
            title = anchor.get_text(" ", strip=True)
            if title:
                found.setdefault(work_id, {"id": work_id, "kind": kind, "title": title, "author": ""})
    return found


def parse_library_ids(page_html: str) -> set[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    ids = set(re.findall(r"""id=["']work-(\d+)["']""", page_html))
    if ids:
        return ids
    for card in soup.select(".bookcard[id]"):
        match = re.fullmatch(r"work-(\d+)", card.get("id", ""))
        if match:
            ids.add(match.group(1))
    return ids


@dataclass
class WorkInfo:
    id: str
    kind: str = "work"
    title: str = ""
    author: str = ""
    genres: list[str] | None = None
    tags: list[str] | None = None
    description: str = ""
    series_id: str = ""
    series_name: str = ""
    series_number: int | None = None

    def __post_init__(self) -> None:
        if self.genres is None:
            self.genres = []
        if self.tags is None:
            self.tags = []

    @property
    def url(self) -> str:
        segment = "audiobook" if self.kind == "audiobook" else "work"
        return f"{BASE}/{segment}/{self.id}"


class WorkCache:
    def __init__(self, path: Path = CACHE_PATH):
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS works (
                work_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at INTEGER NOT NULL
            )"""
        )
        self.db.commit()

    def get(self, work_id: str) -> WorkInfo | None:
        row = self.db.execute(
            "SELECT payload, fetched_at FROM works WHERE work_id = ?", (work_id,)
        ).fetchone()
        if not row or time.time() - row[1] > CACHE_TTL_SECONDS:
            return None
        try:
            return WorkInfo(**json.loads(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def put(self, info: WorkInfo) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO works(work_id, payload, fetched_at) VALUES (?, ?, ?)",
            (info.id, json.dumps(asdict(info), ensure_ascii=False), int(time.time())),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()


class AuthorTodayClient:
    def __init__(self, delay: float = 0.3, log: Callable[[str], None] | None = None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept-Language": "ru,en;q=0.9",
        })
        self.delay = max(0.0, delay)
        self.log = log or (lambda message: None)
        self.cache = WorkCache()
        self.genre_catalog: dict[str, str] | None = None
        self.work_kinds: dict[str, str] = {}

    def close(self) -> None:
        self.cache.close()
        self.session.close()

    def load_cookies(self, path: str) -> None:
        cookie_path = Path(path).expanduser()
        if not cookie_path.is_file():
            raise FileNotFoundError(f"Файл cookies не найден: {cookie_path}")

        loaded = 0
        with cookie_path.open("r", encoding="utf-8", errors="replace") as stream:
            for raw_line in stream:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                elif line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    parts = line.split()
                if len(parts) < 7:
                    continue
                domain, _include_subdomains, cookie_path_value, _secure, _expires, name, value = parts[:7]
                if "author.today" not in domain:
                    continue
                self.session.cookies.set(
                    name, value, domain=domain.lstrip("."), path=cookie_path_value or "/"
                )
                loaded += 1

        if loaded == 0:
            raise ValueError("В cookies.txt не найдено ни одной cookie для author.today.")
        self.log(f"Загружено cookies: {loaded}")

    def get(self, url_or_path: str, retries: int = 3) -> str:
        url = url_or_path if url_or_path.startswith("http") else urljoin(BASE, url_or_path)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                response = self.session.get(url, timeout=35)
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"Не удалось загрузить {url}: {last_error}")

    def ensure_logged_in(self, username: str) -> None:
        html = self.get(f"/u/{username}/library/finished")
        if not re.search(r'"currentUser"\s*:\s*true', html):
            raise RuntimeError(
                "Author.Today не подтвердил авторизацию. Проверь cookies.txt и обнови его "
                "из браузера, где открыт нужный аккаунт."
            )

    def collect_library(self, username: str, state: str, max_pages: int) -> set[str]:
        result: set[str] = set()
        base_path = f"/u/{username}/library/{state}"
        for page in range(1, max_pages + 1):
            path = base_path if page == 1 else f"{base_path}?page={page}"
            html = self.get(path)
            page_ids = parse_library_ids(html)
            result.update(page_ids)
            soup = BeautifulSoup(html, "html.parser")
            self.log(f"Библиотека {state}: страница {page}, всего ID: {len(result)}")
            if not page_has_next(soup, page):
                break
            if page == max_pages:
                self.log(f"ВНИМАНИЕ: для библиотеки {state} достигнут лимит {max_pages} страниц.")
            time.sleep(self.delay)
        return result

    @staticmethod
    def _search_url(tags: list[str], page: int) -> str:
        params: list[tuple[str, str]] = [
            ("category", "works"),
            ("q", ""),
            ("view", "list"),
            ("field", "any"),
            ("sorting", "relevance"),
            ("format", "any"),
        ]
        params.extend(("tags", tag) for tag in tags)
        params.append(("page", str(page)))
        return f"{BASE}/search?{urlencode(params)}"

    def collect_tag_group(self, tags: list[str], max_pages: int) -> set[str]:
        result: set[str] = set()
        label = " | ".join(tags)
        self.log(f"Теги (OR): {label}")
        for page in range(1, max_pages + 1):
            html = self.get(self._search_url(tags, page))
            cards = parse_result_cards(html)
            self.work_kinds.update({work_id: data.get("kind", "work") for work_id, data in cards.items()})
            before = len(result)
            result.update(cards)
            soup = BeautifulSoup(html, "html.parser")
            self.log(f"  Теги: страница {page}, найдено ID: {len(result)}")
            if not cards or not page_has_next(soup, page):
                break
            if page == max_pages:
                self.log(f"  ВНИМАНИЕ: для группы тегов достигнут лимит {max_pages} страниц.")
            time.sleep(self.delay)
            if len(result) == before and page > 2:
                # Keep paging: relevance sorting can shuffle items, so a duplicate page
                # does not prove that pagination is exhausted.
                pass
        return result

    def load_genres(self) -> dict[str, str]:
        if self.genre_catalog is not None:
            return self.genre_catalog
        html = self.get("/work/genres")
        soup = BeautifulSoup(html, "html.parser")
        catalog: dict[str, str] = {}
        for anchor in soup.select('a[href*="/work/genre/"]'):
            href = anchor.get("href", "")
            match = re.search(r"/work/genre/([^/?#]+)", href)
            name = anchor.get_text(" ", strip=True)
            if match and name:
                catalog[normalize(name)] = match.group(1)
        self.genre_catalog = catalog
        self.log(f"Загружен каталог жанров: {len(catalog)}")
        return catalog

    def resolve_genre(self, name: str, catalog: dict[str, str]) -> str:
        normalized = normalize(name)
        if normalized in catalog:
            return catalog[normalized]
        if re.fullmatch(r"[a-z0-9-]+", normalized):
            known_slugs = set(catalog.values())
            if normalized in known_slugs:
                return normalized
        raise ValueError(
            f"Жанр «{name}» не найден в каталоге Author.Today. "
            "Проверь написание или обнови страницу каталога."
        )

    def collect_genre(self, genre_slug: str, max_pages: int) -> set[str]:
        result: set[str] = set()
        base_path = f"/work/genre/{genre_slug}"
        for page in range(1, max_pages + 1):
            path = base_path if page == 1 else f"{base_path}?page={page}"
            html = self.get(path)
            cards = parse_result_cards(html)
            self.work_kinds.update({work_id: data.get("kind", "work") for work_id, data in cards.items()})
            before = len(result)
            result.update(cards)
            soup = BeautifulSoup(html, "html.parser")
            self.log(f"  Жанр {genre_slug}: страница {page}, найдено ID: {len(result)}")
            if not cards or not page_has_next(soup, page):
                break
            if page == max_pages:
                self.log(f"  ВНИМАНИЕ: для жанра {genre_slug} достигнут лимит {max_pages} страниц.")
            time.sleep(self.delay)
            if len(result) == before and page > 2:
                pass
        return result

    def collect_all_works(self, max_pages: int) -> set[str]:
        result: set[str] = set()
        for page in range(1, max_pages + 1):
            path = "/work/genre" if page == 1 else f"/work/genre?page={page}"
            html = self.get(path)
            cards = parse_result_cards(html)
            self.work_kinds.update({work_id: data.get("kind", "work") for work_id, data in cards.items()})
            result.update(cards)
            soup = BeautifulSoup(html, "html.parser")
            self.log(f"Все произведения: страница {page}, найдено ID: {len(result)}")
            if not cards or not page_has_next(soup, page):
                break
            if page == max_pages:
                self.log(f"ВНИМАНИЕ: достигнут лимит {max_pages} страниц.")
            time.sleep(self.delay)
        return result

    @staticmethod
    def _description_from_soup(soup: BeautifulSoup) -> str:
        selectors = [
            "#annotation", ".work-annotation", ".annotation", ".annotation-text",
            ".work-description", ".description", "[itemprop='description']",
            "[id*='annotation']", "[class*='annotation']",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if len(text) > 40:
                    return text

        # Fallback: the annotation tab often links to an element by its id.
        for anchor in soup.find_all("a", href=True):
            if "аннотац" not in normalize(anchor.get_text(" ", strip=True)):
                continue
            target = anchor.get("href", "").lstrip("#")
            if target:
                node = soup.find(id=target)
                if node:
                    text = node.get_text(" ", strip=True)
                    if len(text) > 40:
                        return text
            parent = anchor.find_parent(["section", "div"])
            if parent:
                candidates = parent.find_all(["p", "div"], recursive=True)
                texts = [item.get_text(" ", strip=True) for item in candidates]
                text = max(texts, key=len, default="")
                if len(text) > 40:
                    return text
        return ""

    @staticmethod
    def parse_work_detail(work_id: str, kind: str, page_html: str) -> WorkInfo:
        soup = BeautifulSoup(page_html, "html.parser")
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else ""

        author = ""
        for anchor in soup.select('a[href*="/u/"][href*="/works"]'):
            author = anchor.get_text(" ", strip=True)
            if author:
                break

        # Work metadata appears after the title and before the annotation tabs.
        content_links: list = []
        if h1:
            started = False
            for node in soup.descendants:
                if node is h1:
                    started = True
                    continue
                if not started or getattr(node, "name", None) != "a":
                    continue
                label = normalize(node.get_text(" ", strip=True))
                if label.startswith("аннотац"):
                    break
                content_links.append(node)

        genres: list[str] = []
        tags: list[str] = []
        series_id = ""
        series_name = ""
        series_number: int | None = None

        for anchor in content_links:
            href = anchor.get("href", "")
            label = anchor.get_text(" ", strip=True)
            genre_match = re.search(r"/work/genre/([^/?#]+)", href)
            if genre_match and label and label not in genres:
                genres.append(label)
            if ("/work/tag/" in href or "/tag/" in href) and label and label not in tags:
                tags.append(label)
            series_match = re.search(r"/work/series/(\d+)", href)
            if series_match and not series_id:
                series_id = series_match.group(1)
                series_name = label
                context = anchor.parent.get_text(" ", strip=True) if anchor.parent else label
                number_match = re.search(r"#\s*(\d+)", context)
                if not number_match and anchor.parent and anchor.parent.parent:
                    context = anchor.parent.parent.get_text(" ", strip=True)
                    number_match = re.search(r"#\s*(\d+)", context)
                if number_match:
                    series_number = int(number_match.group(1))

        description = AuthorTodayClient._description_from_soup(soup)
        if not genres:
            # Some layouts place the primary genre link before h1's nearest wrapper.
            for anchor in soup.select('a[href*="/work/genre/"]'):
                label = anchor.get_text(" ", strip=True)
                if label and label not in genres:
                    genres.append(label)
                    if len(genres) >= 12:
                        break

        return WorkInfo(
            id=work_id,
            kind=kind,
            title=title,
            author=author,
            genres=genres,
            tags=tags,
            description=description,
            series_id=series_id,
            series_name=series_name,
            series_number=series_number,
        )

    def get_work_detail(self, work_id: str, kind: str = "work") -> WorkInfo:
        kind = self.work_kinds.get(work_id, kind)
        cached = self.cache.get(work_id)
        if cached:
            return cached
        page_html = self.get(f"/{kind}/{work_id}")
        info = self.parse_work_detail(work_id, kind, page_html)
        self.cache.put(info)
        return info


def apply_cycle_rule(works: list[WorkInfo]) -> list[WorkInfo]:
    """Keep part #1 when it matches; otherwise keep every matching candidate part."""
    by_series: dict[str, list[WorkInfo]] = {}
    standalone: list[WorkInfo] = []
    for work in works:
        if work.series_id:
            by_series.setdefault(work.series_id, []).append(work)
        else:
            standalone.append(work)

    result = list(standalone)
    for series_works in by_series.values():
        first_parts = [work for work in series_works if work.series_number == 1]
        if first_parts:
            result.append(sorted(first_parts, key=lambda w: w.id)[0])
        else:
            result.extend(series_works)
    return result


def run_search(
    *,
    username: str,
    cookies_file: str,
    tags_text: str,
    genres_text: str,
    description_query: str,
    exclude_library: bool,
    collapse_cycles: bool,
    max_pages: int,
    delay: float,
    log: Callable[[str], None],
) -> list[WorkInfo]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", username.strip()):
        raise ValueError("Укажи корректный логин-профиль Author.Today.")
    tag_groups = split_groups(tags_text)
    genre_groups = split_groups(genres_text)

    client = AuthorTodayClient(delay=delay, log=log)
    try:
        if cookies_file.strip():
            client.load_cookies(cookies_file.strip())

        excluded: set[str] = set()
        if exclude_library:
            if not cookies_file.strip():
                raise RuntimeError(
                    "Для исключения личной библиотеки укажи путь к cookies.txt "
                    "из авторизованного браузера."
                )
            client.ensure_logged_in(username.strip())
            finished = client.collect_library(username.strip(), "finished", max_pages)
            disliked = client.collect_library(username.strip(), "disliked", max_pages)
            excluded = finished | disliked
            log(
                f"Исключения из библиотеки загружены: прочитано {len(finished)}, "
                f"не интересно {len(disliked)}, всего уникальных {len(excluded)}."
            )

        group_sets: list[set[str]] = []

        if tag_groups:
            for index, group in enumerate(tag_groups, start=1):
                log(f"Группа тегов {index}/{len(tag_groups)}: {', '.join(group)}")
                group_sets.append(client.collect_tag_group(group, max_pages))

        if genre_groups:
            catalog = client.load_genres()
            for index, group in enumerate(genre_groups, start=1):
                log(f"Группа жанров {index}/{len(genre_groups)}: {', '.join(group)}")
                union: set[str] = set()
                for name in group:
                    slug = client.resolve_genre(name, catalog)
                    union.update(client.collect_genre(slug, max_pages))
                log(f"  В группе жанров найдено уникальных ID: {len(union)}")
                group_sets.append(union)

        if not group_sets:
            log("Фильтры тегов и жанров не заданы: использую общий каталог произведений.")
            group_sets.append(client.collect_all_works(max_pages))

        candidate_ids = intersect_groups(group_sets)
        log(f"После объединения всех групп (AND): {len(candidate_ids)} произведений.")

        details: list[WorkInfo] = []
        need_details = collapse_cycles or bool(description_query.strip())
        if need_details:
            total = len(candidate_ids)
            for index, work_id in enumerate(sorted(candidate_ids, key=int), start=1):
                try:
                    info = client.get_work_detail(work_id)
                    if info.title:
                        details.append(info)
                    else:
                        details.append(WorkInfo(id=work_id))
                except Exception as exc:
                    log(f"Не удалось прочитать карточку {work_id}: {exc}")
                    details.append(WorkInfo(id=work_id))
                if index % 25 == 0 or index == total:
                    log(f"Карточки произведений: {index}/{total}")
                time.sleep(client.delay)

            if description_query.strip():
                tokens = [token for token in normalize(description_query).split() if token]
                before = len(details)
                details = [
                    work for work in details
                    if work.description and all(token in normalize(work.description) for token in tokens)
                ]
                missing = sum(1 for work in details if not work.description)
                log(
                    f"Поиск по описанию: осталось {len(details)} из {before}. "
                    "Все введённые слова должны присутствовать в аннотации."
                )
            if collapse_cycles:
                before = len(details)
                details = apply_cycle_rule(details)
                log(f"Правило циклов: {before} → {len(details)} произведений.")

        else:
            details = [WorkInfo(id=work_id, kind=client.work_kinds.get(work_id, "work")) for work_id in candidate_ids]

        # Apply personal-library exclusions after the cycle rule. Thus, if the first
        # volume matches the chosen filters but is already read/disliked, sequels
        # will not reappear as substitutes.
        if exclude_library:
            before = len(details)
            details = [work for work in details if work.id not in excluded]
            log(f"Исключение библиотеки: {before} → {len(details)} произведений.")

        # If details were not requested, fill in titles with one cached/page lookup
        # only for the final set.
        if not need_details:
            filled: list[WorkInfo] = []
            for index, work in enumerate(details, start=1):
                try:
                    info = client.get_work_detail(work.id)
                    filled.append(info)
                except Exception as exc:
                    log(f"Не удалось получить название {work.id}: {exc}")
                    filled.append(work)
                if index % 25 == 0 or index == len(details):
                    log(f"Получение названий: {index}/{len(details)}")
                time.sleep(client.delay)
            details = filled

        details.sort(key=lambda work: normalize(work.title or work.id))
        log(f"Готово. Итоговых произведений: {len(details)}.")
        return details
    finally:
        client.close()


class AdvancedSearchApp:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.filedialog = filedialog
        self.root = root
        self.root.title("Author.Today — расширенный поиск")
        self.root.geometry("1250x850")
        self.events: queue.Queue = queue.Queue()
        self.results: list[WorkInfo] = []

        self.username = tk.StringVar(value="daniilmantus")
        self.cookies_file = tk.StringVar()
        self.description = tk.StringVar()
        self.exclude_library = tk.BooleanVar(value=True)
        self.collapse_cycles = tk.BooleanVar(value=True)
        self.max_pages = tk.StringVar(value="200")
        self.delay = tk.StringVar(value="0.3")

        self._build()
        self.root.after(100, self._poll_events)

    def _build(self):
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        account = ttk.LabelFrame(outer, text="Аккаунт Author.Today", padding=8)
        account.pack(fill="x")
        ttk.Label(account, text="Логин профиля:").grid(row=0, column=0, sticky="w")
        ttk.Entry(account, textvariable=self.username, width=24).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(account, text="cookies.txt:").grid(row=0, column=2, sticky="w", padx=(20, 0))
        ttk.Entry(account, textvariable=self.cookies_file).grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Button(account, text="Обзор…", command=self._browse_cookies).grid(row=0, column=4)
        account.columnconfigure(3, weight=1)

        filters = ttk.Frame(outer)
        filters.pack(fill="x", pady=(10, 8))
        tag_box = ttk.LabelFrame(filters, text="Теги", padding=8)
        genre_box = ttk.LabelFrame(filters, text="Жанры", padding=8)
        tag_box.pack(side="left", fill="both", expand=True, padx=(0, 5))
        genre_box.pack(side="left", fill="both", expand=True, padx=(5, 0))
        ttk.Label(tag_box, text="Одна строка = обязательная группа; через запятую = варианты (OR).").pack(anchor="w")
        ttk.Label(genre_box, text="Та же логика: строки объединяются через AND, варианты через OR.").pack(anchor="w")
        self.tags_text = tk.Text(tag_box, height=5, wrap="word")
        self.genres_text = tk.Text(genre_box, height=5, wrap="word")
        self.tags_text.pack(fill="both", expand=True, pady=(4, 0))
        self.genres_text.pack(fill="both", expand=True, pady=(4, 0))
        self.tags_text.insert("1.0", "")
        self.genres_text.insert("1.0", "")

        description_box = ttk.LabelFrame(outer, text="Поиск по описанию (необязательно)", padding=8)
        description_box.pack(fill="x")
        ttk.Entry(description_box, textvariable=self.description).pack(fill="x")
        ttk.Label(
            description_box,
            text="Все введённые слова должны встретиться в аннотации. Поиск выполняется локально по карточкам найденных произведений.",
            foreground="#555555",
        ).pack(anchor="w", pady=(4, 0))

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=8)
        ttk.Checkbutton(
            options, text="Исключать «Прочитано» и «Не интересно»",
            variable=self.exclude_library,
        ).pack(side="left")
        ttk.Checkbutton(
            options, text="Применять правило первой книги цикла",
            variable=self.collapse_cycles,
        ).pack(side="left", padx=(18, 0))
        ttk.Label(options, text="Макс. страниц на один запрос:").pack(side="left", padx=(18, 4))
        ttk.Entry(options, textvariable=self.max_pages, width=7).pack(side="left")
        ttk.Label(options, text="Пауза, сек:").pack(side="left", padx=(12, 4))
        ttk.Entry(options, textvariable=self.delay, width=6).pack(side="left")
        self.search_button = ttk.Button(options, text="Найти произведения", command=self._start_search)
        self.search_button.pack(side="right")

        result_frame = ttk.LabelFrame(outer, text="Результаты", padding=8)
        result_frame.pack(fill="both", expand=True)
        columns = ("title", "author", "genres", "series", "id")
        self.tree = ttk.Treeview(result_frame, columns=columns, show="headings", height=15)
        headings = {
            "title": ("Произведение", 350),
            "author": ("Автор", 170),
            "genres": ("Жанры", 300),
            "series": ("Цикл", 220),
            "id": ("ID", 75),
        }
        for column, (label, width) in headings.items():
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w")
        yscroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(result_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self._open_selected)

        result_buttons = ttk.Frame(outer)
        result_buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(result_buttons, text="Открыть выбранное", command=self._open_selected).pack(side="left")
        ttk.Button(result_buttons, text="Экспорт CSV…", command=self._export_csv).pack(side="left", padx=6)
        self.status = tk.StringVar(value="Готов к поиску.")
        ttk.Label(result_buttons, textvariable=self.status).pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="Ход поиска", padding=6)
        log_frame.pack(fill="x", pady=(8, 0))
        self.log_text = tk.Text(log_frame, height=7, wrap="word", state="disabled")
        self.log_text.pack(fill="x")

    def _browse_cookies(self):
        path = self.filedialog.askopenfilename(
            title="Выбери cookies.txt",
            filetypes=[("Cookies TXT", "*.txt"), ("Все файлы", "*.*")],
        )
        if path:
            self.cookies_file.set(path)

    def _append_log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start_search(self):
        if self.search_button.instate(["disabled"]):
            return
        try:
            max_pages = int(self.max_pages.get())
            delay = float(self.delay.get())
            if max_pages < 1 or delay < 0:
                raise ValueError
        except ValueError:
            self.messagebox.showerror("Параметры", "Максимум страниц должен быть ≥ 1, пауза — ≥ 0.")
            return

        self.tree.delete(*self.tree.get_children())
        self.results = []
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.search_button.configure(state="disabled")
        self.status.set("Поиск выполняется…")

        args = {
            "username": self.username.get().strip(),
            "cookies_file": self.cookies_file.get().strip(),
            "tags_text": self.tags_text.get("1.0", "end").strip(),
            "genres_text": self.genres_text.get("1.0", "end").strip(),
            "description_query": self.description.get().strip(),
            "exclude_library": self.exclude_library.get(),
            "collapse_cycles": self.collapse_cycles.get(),
            "max_pages": max_pages,
            "delay": delay,
        }
        threading.Thread(target=self._worker, kwargs=args, daemon=True).start()

    def _worker(self, **kwargs):
        try:
            found = run_search(log=lambda line: self.events.put(("log", line)), **kwargs)
            self.events.put(("results", found))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("done", None))

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "results":
                    self.results = payload
                    for index, work in enumerate(payload):
                        series = work.series_name
                        if work.series_number:
                            series += f" #{work.series_number}"
                        self.tree.insert(
                            "", "end", iid=str(index),
                            values=(
                                work.title or f"Произведение {work.id}",
                                work.author,
                                ", ".join(work.genres or []),
                                series,
                                work.id,
                            ),
                        )
                    self.status.set(f"Найдено: {len(payload)}")
                elif kind == "error":
                    self.messagebox.showerror("Ошибка поиска", payload)
                    self.status.set("Поиск завершился с ошибкой.")
                elif kind == "done":
                    self.search_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _selected_work(self) -> WorkInfo | None:
        selected = self.tree.selection()
        if not selected:
            return None
        try:
            return self.results[int(selected[0])]
        except (ValueError, IndexError):
            return None

    def _open_selected(self, _event=None):
        work = self._selected_work()
        if work:
            webbrowser.open(work.url)

    def _export_csv(self):
        if not self.results:
            self.messagebox.showinfo("Экспорт", "Сначала выполни поиск.")
            return
        path = self.filedialog.asksaveasfilename(
            title="Сохранить результаты",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="author_today_search.csv",
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["Название", "Автор", "Жанры", "Теги", "Описание", "Цикл", "Номер", "Ссылка"])
            for work in self.results:
                writer.writerow([
                    work.title, work.author, ", ".join(work.genres or []),
                    ", ".join(work.tags or []), work.description,
                    work.series_name, work.series_number or "", work.url,
                ])
        self.messagebox.showinfo("Экспорт", f"Сохранено: {path}")


def main() -> None:
    import tkinter as tk
    root = tk.Tk()
    AdvancedSearchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
