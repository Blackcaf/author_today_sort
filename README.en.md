# strict_tag_search - a smart book search for author.today

**Strict group-based search** by tags: search for books that match several tag **groups**
at once, gather results from all pages, deduplicate and print the final list with direct
links. No more tedious manual pagination in the browser.

The script is pure Python + `requests` and parses the author.today HTML directly.

---

## Why bother?

The built-in author.today search - it's **one plain query** and **one topic** per request.
It can't do:

- books that have **several tag groups at once** (AND of groups);
- **spelling variants** of the same tag merged into one group (OR);
- **the full result set** (results are sorted by *relevance*, drift between pages, and
  part of the books is lost on the way).

This script closes all three gaps:

- tags inside a group (comma-separated) work as **OR**;
- separate arguments (groups) are all **required** = **AND**;
- it walks up to **200 pages** by default, collects every unique work, then reports the
  site's own result count and compares it with what was actually gathered.

---

## Features

- **Grouped search with OR/AND logic** - tags in a group are comma-separated, groups are
  space-separated arguments.
- **Finished ebooks only** by default; audio and unfinished works are opt-in via flags.
- **Deduplication** - the same work id is reported once even if it shows up on many pages.
- **Site-count cross-check** - if pagination shuffles books, the script honestly tells
  you about the (mis)match.
- **Three auth strategies** - e-mail/password, raw cookies from the browser, or a
  `cookies.txt` file in the Netscape/curl format (the most reliable one).
- **Retries on network errors** (up to 3 attempts with a pause), a crawl delay, and a
  page limit - so you don't trip the anti-bot protection.
- **Quick start** - `pip install requests` and you're done.

---

## Installation 📦

Python 3.8+ required (tested on 3.14 too).

```bash
pip install requests
```

(Or create a `requirements.txt` file with a line `requests>=2.28,<3`.)

---

## Usage example

```bash
python strict_tag_search.py "game system, system" "marvel, dc" [--options]
```

### How to read the tag arguments

- **Inside one group** - these are **alternatives** of the same tag, comma-separated.
  For example, if a book is tagged only with a certain spelling of a tag, it will still
  be found by another spelling from the same group. Logic: `OR`.
- **A whole argument** - a separate group. **All groups are mandatory** at the same time.
  Logic: `AND`.

Example: a book must be **both** about a system and about Marvel or DC.

> If you need to filter by **one** tag without variants - just pass it as a single
> argument: `python strict_tag_search.py "fantasy"`.

---

## CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `tags` | - | One or more arguments-groups with tags (required parameter) |
| `--max-pages N` | `200` | Max pages per query. Stops on the site's own pagination; the limit is a safety cap against infinite loops |
| `--delay SEC` | `0.3` | Pause between HTTP requests in seconds (politeness to the site) |
| `--any-state` | off | Do not filter by status - also include **unfinished** works |
| `--with-audio` | off | Include **audiobooks** in results (eBooks by default) |
| `--email` | - | author.today account email/login for login-based auth |
| `--password` | - | Password (requires `--email`) |
| `--cookie` | - | Browser cookies as one string: `"name=value; name2=value2"` |
| `--cookies-file PATH` | - | Path to `cookies.txt` (**Netscape/curl format**; export from a browser extension https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc?hl=ru&utm_source=ext_sidebar) |

### Examples

```bash
# Simple single-tag search
python strict_tag_search.py "isekai"

# Grouped search: harem + Marvel (spelling variants inside each group)
python strict_tag_search.py "harem, haremnik" "marvel 11, marvel11"

# Same but + audiobooks and unfinished works
python strict_tag_search.py "harem, haremnik" "marvel, marvel 11" --with-audio --any-state

# Cookies from the browser (a reliable way to authenticate)
python strict_tag_search.py "mystery" --cookie "_identity=abc...; _session_id=xyz..."

# Cookies from a Netscape/curl cookies.txt file
python strict_tag_search.py "litrpg" --cookies-file "cookies.txt"

# Login with email/password
python strict_tag_search.py "horror" --email me@example.com --password S3cret
```

---

## Authentication

Login is not strictly required for searching - the site returns results to anonymous
visitors too, though fewer results. But you can authenticate:

1. **via a cookies file** (the most reliable way): log in to your account in a browser,
   export cookies via an extension, e.g. *Get cookies.txt LOCALLY*, then:
   `--cookies-file "cookies.txt"`. The file contains rows of format
   `domain  includeSubdomains  path  secure  expiry  name  value` - the script parses it itself.
2. **as a cookies string** from DevTools (*Application · Cookies*):
   `--cookie "_identity=abc...; _session_id=xyz..."`.
3. **login/password** - the script will fetch the CSRF token and log in (`--email` +
   `--password`). If **two-factor authentication (2FA)** is enabled on the account,
   scripted login won't work - use the cookie ways.

After starting with any auth method the script checks whether we are logged in and warns
if the session is not active (then the search falls back to anonymous).

---

## What the script outputs

To **stderr**:
- for each group: `Group N: <tags>`, the site's result count and how many unique were collected;
- warnings about hitting `--max-pages` and any mismatch with the site's count.

To **stdout** (the useful part):
- the final count: either "as on the site" + collected unique (with a note about the
  mismatch ★), or the number of books satisfying **all** groups;
- then a list: `  - <title>  https://author.today/work/<id>` (or `/audiobook/<id>`).

Example output:

```
Group 1: harem | haremhik
  Single query with all group tags...
  Results on site: 1234 (collected: 1231)
Group 2: marvel 11 | marvel,marvel 11

Results for the query (as on site): 123 (collected unique: 121)
  [!] Difference (-2) - relevance sorting shifts the list between pages.
  - Harem Marvel Day  https://author.today/work/12345
  - ...
```

A handy pattern - save the links to a file:

```bash
python strict_tag_search.py "fantasy" > result.txt
```

---

## Careful when scraping

- Add a bigger `--delay` for large page lists so you don't overload the site.
- Don't fire thousands of requests a minute - author.today may IP-ban you.
- The script retries a failed request 3 times with a 1.5 s pause, but that is not a cure-all.

---

## Project structure

```
author_today_sort/
├── strict_tag_search.py   # the main search script
├── README.md              # documentation
└── .gitignore
```

---

## Small caveats

- The search parses the HTML page of `https://author.today/search`. If author.today
  changes its markup, some regexes will need a small fix.
- Results are sorted by "relevance", so the lists may "drift" between pages - the script
  collects unique ones and honestly reports the difference.
- The script is meant for personal use; don't use it in production without tuning the limits.

---

## License

Licensed under the **Apache License 2.0** (see [LICENSE](LICENSE)).
© 2026 NLSHAKAL (Daniil). Use it responsibly and respect the site. 🙌