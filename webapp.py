from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid

from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from advanced_search import AuthorTodayClient, run_search

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_COOKIE_FILE_SIZE = 2 * 1024 * 1024
MAX_PARALLEL_SEARCHES = 1
app = FastAPI(
    title="Author.Today Search",
    description="Локальный веб-интерфейс расширенного поиска Author.Today",
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_local_caching(request: Request, call_next):
    """Keep the locally served UI fresh after code updates."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()

def _cleanup_jobs() -> None:
    cutoff = time.time() - 60 * 60
    with _jobs_lock:
        old = [
            job_id for job_id, job in _jobs.items()
            if job["created_at"] < cutoff and job["status"] in {"completed", "failed"}
        ]
        for job_id in old:
            _jobs.pop(job_id, None)


def _running_count() -> int:
    with _jobs_lock:
        return sum(1 for job in _jobs.values() if job["status"] in {"queued", "running"})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _parse_genre_catalog(page_html: str) -> list[dict[str, str]]:
    """Extract only real genre routes, not navigation links such as /genre/all/ebook."""
    soup = BeautifulSoup(page_html, "html.parser")
    items: dict[str, dict[str, str]] = {}
    for anchor in soup.select('a[href*="/work/genre/"]'):
        href = anchor.get("href", "")
        name = anchor.get_text(" ", strip=True)
        path_parts = [part for part in urlsplit(href).path.strip("/").split("/") if part]
        if len(path_parts) != 3 or path_parts[:2] != ["work", "genre"]:
            continue
        slug = path_parts[2]
        if not name or slug.casefold() == "all":
            continue
        items[slug] = {"name": name, "slug": slug}
    return sorted(items.values(), key=lambda item: item["name"].casefold())


def _parse_genre_payload(payload: Any) -> list[dict[str, str]]:
    """Validate and normalize the locally bundled Author.Today genre catalog."""
    if not isinstance(payload, dict) or not isinstance(payload.get("genres"), list):
        raise ValueError("В локальном каталоге отсутствует список жанров.")

    items: dict[str, dict[str, str]] = {}
    for item in payload["genres"]:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name", "")).split())
        slug = str(item.get("slug", "")).strip()
        if not name or not slug:
            continue
        items[slug] = {"name": name, "slug": slug}

    if not items:
        raise ValueError("Локальный каталог жанров пуст.")
    return sorted(items.values(), key=lambda item: item["name"].casefold())


@lru_cache(maxsize=1)
def _load_local_genre_catalog() -> list[dict[str, str]]:
    """Load the bundled genre catalog once per server process."""
    catalog_path = STATIC_DIR / "genres.json"
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать локальный каталог жанров: {exc}") from exc
    return _parse_genre_payload(payload)


def _parse_tag_catalog(payload: Any) -> list[str]:
    """Validate and normalize the locally bundled Author.Today tag catalog."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tags"), list):
        raise ValueError("В локальном каталоге отсутствует список тегов.")

    tags: list[str] = []
    seen: set[str] = set()
    for item in payload["tags"]:
        if not isinstance(item, str):
            continue
        title = " ".join(item.split())
        # Preserve ё/e spelling variants: Author.Today treats them as distinct labels.
        key = title.casefold()
        if title and key not in seen:
            seen.add(key)
            tags.append(title)

    if not tags:
        raise ValueError("Локальный каталог тегов пуст.")
    return tags


@lru_cache(maxsize=1)
def _load_tag_catalog() -> list[str]:
    """Load the bundled tag catalog once per server process."""
    catalog_path = STATIC_DIR / "tags.json"
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать локальный каталог тегов: {exc}") from exc
    return _parse_tag_catalog(payload)


def _normalize_tag_text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _suggest_tag_names(query: str, catalog: list[str], limit: int = 12) -> list[str]:
    """Find local catalog matches, preferring prefixes and preserving popularity order."""
    normalized_query = _normalize_tag_text(query)
    if len(normalized_query) < 2:
        return []

    matches = [
        name for name in catalog
        if normalized_query in _normalize_tag_text(name)
    ]
    # Python sorting is stable, so the original catalog popularity order is retained
    # within the prefix and substring groups.
    matches.sort(
        key=lambda name: not _normalize_tag_text(name).startswith(normalized_query)
    )
    return matches[:max(1, limit)]


@app.get("/api/genres")
def genres() -> dict[str, Any]:
    """Return the bundled genre directory, with a live fallback if it is missing."""
    try:
        return {"genres": _load_local_genre_catalog(), "source": "local_catalog"}
    except Exception as local_exc:
        client = AuthorTodayClient(delay=0.0)
        try:
            page_html = client.get("/work/genres")
            catalog = _parse_genre_catalog(page_html)
            if not catalog:
                raise RuntimeError("Каталог жанров вернул пустой список.")
            return {"genres": catalog, "source": "author_today"}
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Не удалось загрузить жанры Author.Today: "
                    f"{exc}. Локальный каталог: {local_exc}"
                ),
            ) from exc
        finally:
            client.close()


@app.get("/api/tag-suggestions")
def tag_suggestions(q: str = "") -> dict[str, Any]:
    """Suggest exact tag names from the bundled local catalog (no external API call)."""
    query = " ".join(q.split())
    if len(_normalize_tag_text(query)) < 2:
        return {"tags": [], "query": query, "source": "local_catalog"}

    try:
        suggestions = _suggest_tag_names(query, _load_tag_catalog(), limit=12)
        return {"tags": suggestions, "query": query, "source": "local_catalog"}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось загрузить локальный каталог тегов: {exc}",
        ) from exc


def _run_job(
    job_id: str,
    cookie_path: str | None,
    params: dict[str, Any],
) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            if cookie_path:
                Path(cookie_path).unlink(missing_ok=True)
            return
        job["status"] = "running"
        job["updated_at"] = time.time()

    def log(message: str) -> None:
        with _jobs_lock:
            current = _jobs.get(job_id)
            if current:
                current["logs"].append(str(message))
                current["updated_at"] = time.time()

    try:
        result = run_search(
            username=params["username"],
            cookies_file=cookie_path or "",
            tags_text=params["tags"],
            genres_text=params["genres"],
            description_query=params["description"],
            exclude_library=params["exclude_library"],
            collapse_cycles=params["collapse_cycles"],
            max_pages=params["max_pages"],
            delay=params["delay"],
            only_finished=params["only_finished"],
            log=log,
        )
        serializable = []
        for work in result:
            item = asdict(work)
            item["url"] = work.url
            serializable.append(item)
        with _jobs_lock:
            current = _jobs.get(job_id)
            if current:
                current["results"] = serializable
                current["status"] = "completed"
                current["updated_at"] = time.time()
    except Exception as exc:
        log(f"Ошибка: {exc}")
        with _jobs_lock:
            current = _jobs.get(job_id)
            if current:
                current["error"] = str(exc)
                current["status"] = "failed"
                current["updated_at"] = time.time()
    finally:
        if cookie_path:
            try:
                Path(cookie_path).unlink(missing_ok=True)
            except OSError:
                pass


@app.post("/api/search")
async def start_search(
    username: str = Form(default=""),
    tags: str = Form(default=""),
    genres: str = Form(default=""),
    description: str = Form(default=""),
    exclude_library: bool = Form(default=True),
    collapse_cycles: bool = Form(default=True),
    only_finished: bool = Form(default=True),
    max_pages: int = Form(default=200),
    delay: float = Form(default=0.3),
    cookies: UploadFile | None = File(default=None),
) -> dict[str, str]:
    _cleanup_jobs()

    if _running_count() >= MAX_PARALLEL_SEARCHES:
        raise HTTPException(status_code=409, detail="Другой поиск уже выполняется. Дождись его завершения.")

    username = username.strip()
    if not username or len(username) > 80:
        raise HTTPException(status_code=422, detail="Укажи логин профиля Author.Today.")
    if max_pages < 1 or max_pages > 1000:
        raise HTTPException(status_code=422, detail="Лимит страниц должен быть от 1 до 1000.")
    if delay < 0 or delay > 30:
        raise HTTPException(status_code=422, detail="Пауза должна быть от 0 до 30 секунд.")
    if not tags.strip() and not genres.strip() and not description.strip():
        raise HTTPException(status_code=422, detail="Добавь хотя бы один тег, жанр или слово для поиска.")

    cookie_path: str | None = None
    if cookies is not None and cookies.filename:
        payload = await cookies.read()
        if len(payload) > MAX_COOKIE_FILE_SIZE:
            raise HTTPException(status_code=413, detail="Файл cookies слишком большой (лимит 2 МБ).")
        if payload:
            fd, cookie_path = tempfile.mkstemp(prefix="author_today_cookies_", suffix=".txt")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
            except Exception:
                Path(cookie_path).unlink(missing_ok=True)
                raise
    if exclude_library and not cookie_path:
        raise HTTPException(
            status_code=422,
            detail="Чтобы исключить «Прочитано» и «Не интересно», прикрепи cookies.txt из авторизованного браузера.",
        )

    job_id = uuid.uuid4().hex
    params = {
        "username": username,
        "tags": tags,
        "genres": genres,
        "description": description,
        "exclude_library": exclude_library,
        "collapse_cycles": collapse_cycles,
        "only_finished": only_finished,
        "max_pages": max_pages,
        "delay": delay,
    }
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "logs": ["Поиск добавлен в очередь."],
            "results": None,
            "error": None,
        }

    worker = threading.Thread(
        target=_run_job,
        args=(job_id, cookie_path, params),
        daemon=True,
        name=f"author-today-search-{job_id[:8]}",
    )
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, after: int = 0) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Задание поиска не найдено или уже удалено.")
        logs = job["logs"]
        safe_after = max(0, min(after, len(logs)))
        return {
            "id": job["id"],
            "status": job["status"],
            "logs": logs[safe_after:],
            "next_log": len(logs),
            "results": job["results"] if job["status"] == "completed" else None,
            "error": job["error"],
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webapp:app", host="127.0.0.1", port=8765, reload=False)
