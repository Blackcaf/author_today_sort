#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grouped tag search on author.today.

Site behaviour: multiple `tags=` params are joined with OR (a book matches if
it has *any* of the tags). This script adds grouping logic:

  * within one CLI argument, tags separated by comma are ALTERNATIVES (OR)
  * between CLI arguments the logic is STRICT (AND)

A book is returned only if it satisfies every group.

By default only finished ebooks are kept (no audio books, no in-progress).
Use --any-state / --with-audio to relax those filters.

Usage:
    python strict_tag_search.py "гарем,гаремник" "марвел11,марвел 11"
    python strict_tag_search.py --max-pages 5 "гарем" "эротика"
"""

import argparse
import html
import re
import sys
import time
from urllib.parse import quote

import requests

BASE = "https://author.today"
WORK_RE = re.compile(r'href="/(?P<kind>work|audiobook)/(\d+)"')


def fetch(url, session, retries=3):
    last = None
    for _ in range(retries):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return html.unescape(r.text)
        except requests.RequestException as e:
            last = e
            time.sleep(1.5)
    raise SystemExit(f"Не удалось загрузить {url}: {last}")


def collect_works_for_tag(tags, session, max_pages, delay, only_finished, only_ebooks):
    """Return (id_set, meta:{id:(kind,title)}) for a single query.

    tags: list of tags — each becomes its own `tags=` param on the SAME
    request, exactly like the site's search UI does it (they're the "all
    these tags" filter). Page-count then matches the site's own number.
    """
    ids = set()
    meta = {}
    total = None
    page = 1
    label = " | ".join(tags)
    truncated = True
    while page <= max_pages:
        params = [
            "category=works",
            "q=&view=list&field=any&sorting=relevance",
        ]
        params.append("format=ebook" if only_ebooks else "format=any")
        if only_finished:
            params.append("finished=true")
        for tag in tags:
            params.append(f"tags={quote(tag)}")
        params.append(f"page={page}")
        url = f"{BASE}/search?" + "&".join(params)
        text = fetch(url, session)

        # server-side total shown by the site ("Результатов: N")
        if page == 1:
            m = re.search(r'Результатов:\s*([\d\s]+)', text)
            if m:
                total = int(m.group(1).replace(" ", ""))

        # split into cards; each card contains either work or audiobook href twice
        rows = text.split('<div class="book-row">')[1:]
        if not rows:
            break  # no more results on this page

        found_any = False
        for row in rows:
            m = WORK_RE.search(row)
            if not m:
                continue
            kind, wid = m.group(1), m.group(2)
            if kind not in ("work", "audiobook"):
                continue
            tm = re.search(r'class="book-title">\s*<a[^>]*>([^<]+)</a>', row)
            title = html.unescape(tm.group(1)).strip() if tm else ""
            ids.add(wid)
            meta.setdefault(wid, {"kind": kind, "title": title})
            found_any = True

        if not found_any:
            break

        if page % 5 == 0:
            sys.stderr.write(f"  [{label}] страница {page}, уникальных: {len(ids)}\n")

        # pagination: continue if a link to (page+1) exists;
        # stop early once collected as many unique books as the server total
        if total is not None and len(ids) >= total:
            truncated = False
            break
        nxt = re.search(rf'page={page + 1}(?:&|"|&amp;)', text)
        if not nxt:
            truncated = False
            break
        page += 1
        time.sleep(delay)

    if truncated:
        sys.stderr.write(
            f"  [!{label}] достигнут --max-pages={max_pages}, возможно не все результаты.\n"
        )

    return ids, meta, total


def split_groups(args_tags):
    """Each CLI argument is a group: alternatives split by comma (OR)."""
    groups = []
    for raw in args_tags:
        alts = [a.strip() for a in raw.split(",") if a.strip()]
        if not alts:
            continue
        groups.append(alts)
    return groups


def parse_cookie_string(cookie_str, session):
    """Parse 'name=value; name2=value2' into session cookies."""
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        session.cookies.set(name.strip(), value.strip(), domain=".author.today")
    sys.stderr.write(f"Загружено куки: {[c.name for c in session.cookies]}\n")


def load_cookies_from_file(path, session):
    """Load cookies from a Netscape-format cookies.txt (curl/wget)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path_, secure, expires, name, value = parts[:7]
            if "author.today" in domain:
                session.cookies.set(name, value, domain=domain.lstrip("."), path=path_ or "/")
    sys.stderr.write(f"Загружено куки из файла: {[k for k, _ in session.cookies.items()]}\n")


def login(session, email, password):
    """Log in via POST /account/login (may require 2FA code on a new device)."""
    login_url = BASE + "/account/login"
    r = session.get(login_url, timeout=30)
    r.raise_for_status()
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]*)"', r.text)
    if not m:
        raise SystemExit("Не удалось получить CSRF-токен для входа")
    token = m.group(1)
    data = {
        "__RequestVerificationToken": token,
        "SendEmailIfNeeded": "",
        "Login": email,
        "Password": password,
        "RememberMe": "false",
        "Code": "",
    }
    r = session.post(login_url, data=data, timeout=30, allow_redirects=False)
    if r.status_code in (302, 303):
        loc = r.headers.get("Location", "")
        sys.stderr.write(f"Вход выполнен (redirect -> {loc}).\n")
        session.get(BASE + (loc if loc.startswith("/") else "/"), timeout=30)
        return True
    # 200: вернулась форма входа с ошибкой или кодом 2FA
    err = re.search(r'(error-messages[^>]*>[\s\S]{0,200}?<)', r.text)
    err2 = re.search(r'Неправильный|некоррект|ошибка|не найден|Заблокирован', r.text, re.IGNORECASE)
    twofa = "Введите код подтверждения" in r.text or 'name="Code"' in r.text
    if twofa:
        sys.stderr.write(
            "Требуется код подтверждения (2FA/новое устройство). "
            "Используй --cookie или --cookies-file из залогиненного браузера.\n"
        )
        return False
    raise SystemExit(
        "Вход не удался. Проверь email/пароль. Совет: возьми куки из браузера через --cookie/--cookies-file."
    )


def is_logged_in(session):
    """True if session is authenticated (check for user-only markers)."""
    text = fetch(BASE + "/", session)
    # аноним видит 'Войти' / кнопку регистрации в шапке; залогиненный — нет
    return ("Войти" not in text
            and "/account/register" not in text
            and "Моя библиотека" in text)


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Групповой (строгий) поиск книг на author.today. "
            "Каждый аргумент — группа: теги внутри через запятую = варианты (OR), "
            "между аргументами — все группы обязательны (AND). "
            "По умолчанию: только завершённые электронные книги (без аудио)."
        )
    )
    ap.add_argument(
        "tags",
        nargs="+",
        help='группы тегов, например: "гарем,гаремник" "марвел11,марвел 11"',
    )
    ap.add_argument("--max-pages", type=int, default=200, help="макс. страниц на запрос (по умолч. 200; упрётся сам по пагинации сайта)")
    ap.add_argument("--delay", type=float, default=0.3, help="пауза между запросами, сек (по умолч. 0.3)")
    ap.add_argument("--any-state", action="store_true", help="не фильтровать по статусу (и незаконченные)")
    ap.add_argument("--with-audio", action="store_true", help="включить аудиокниги в результаты")
    ap.add_argument("--email", help="email/логин аккаунта author.today для входа")
    ap.add_argument("--password", help="пароль от аккаунта (или передать леса события)")
    ap.add_argument("--cookie", help='куки из браузера в виде "name=value; name2=value2"')
    ap.add_argument("--cookies-file", help="путь к cookies.txt (формат Netscape/curl)")
    args = ap.parse_args()
    if args.password and not args.email:
        ap.error("--password требует --email")

    groups = split_groups(args.tags)
    if not groups:
        ap.error("укажи хотя бы один тег")

    only_finished = not args.any_state
    only_ebooks = not args.with_audio

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) strict-tag-search"}
    )

    if args.cookie:
        parse_cookie_string(args.cookie, session)
    if args.cookies_file:
        load_cookies_from_file(args.cookies_file, session)
    if args.email:
        login(session, args.email, args.password or "")
    if args.cookie or args.cookies_file or args.email:
        try:
            if is_logged_in(session):
                sys.stderr.write("Сессия активна (залогинен).\n")
            else:
                sys.stderr.write("Предупреждение: войти не удалось, поиск пойдёт как для анонима.\n")
        except Exception as e:
            sys.stderr.write(f"Предупреждение: не удалось проверить сессию: {e}\n")

    # per group: one request with all its tags; then strict AND between groups
    group_sets = []
    group_totals = {}
    meta = {}
    for gi, alts in enumerate(groups, start=1):
        union = set()
        label = " | ".join(alts)
        sys.stderr.write(f"Группа {gi}: {label}\n")
        sys.stderr.write(f"  Ищу одним запросом со всеми тегами группы...\n")
        ids, m, total = collect_works_for_tag(
            alts, session, args.max_pages, args.delay, only_finished, only_ebooks
        )
        union |= ids
        for wid, d in m.items():
            meta.setdefault(wid, d)
        if total is not None:
            sys.stderr.write(f"  Результатов на сайте: {total} (собрано: {len(union)})\n")
        sys.stderr.write(f"  Итого в группе: {len(union)}\n")
        group_totals[gi] = total
        group_sets.append(union)

    base = None
    for s in group_sets:
        base = set(s) if base is None else (base & set(s))
    common = sorted(base, key=int) if base else []

    labels = [" | ".join(alts) for alts in groups]
    if len(group_sets) == 1 and group_totals.get(1) is not None:
        official = group_totals[1]
        delta = len(common) - official
        sign = "+" if delta > 0 else ""
        print(
            f"\nРезультатов по запросу (как на сайте): {official}"
            f" (собрано уникальных: {len(common)})"
        )
        if delta != 0:
            print(
                f"  [!] Расхождение ({sign}{delta}) — сортировка по релевантности "
                "сдвигает список между страницами."
            )
    else:
        print(
            f"\nКниг, удовлетворяющих ВСЕМ группам ({len(groups)} шт.): {len(common)}"
        )
    for wid in common:
        d = meta.get(wid, {})
        kind = d.get("kind", "work")
        title = d.get("title", "")
        url = f"{BASE}/work/{wid}" if kind == "work" else f"{BASE}/audiobook/{wid}"
        print(f"  - {title or wid}  {url}")
    if not common:
        print("  Ничего не найдено.")


if __name__ == "__main__":
    main()