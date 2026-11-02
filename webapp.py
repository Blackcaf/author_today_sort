from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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


@app.get("/api/genres")
def genres() -> dict[str, Any]:
    """Return the current public genre directory for autocomplete."""
    client = AuthorTodayClient(delay=0.0)
    try:
        page_html = client.get("/work/genres")
        soup = BeautifulSoup(page_html, "html.parser")
        items: dict[str, dict[str, str]] = {}
        for anchor in soup.select('a[href*="/work/genre/"]'):
            href = anchor.get("href", "")
            name = anchor.get_text(" ", strip=True)
            slug_part = href.split("/work/genre/", 1)[-1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
            if not name or not slug_part:
                continue
            items[slug_part] = {"name": name, "slug": slug_part}
        return {"genres": sorted(items.values(), key=lambda item: item["name"].casefold())}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось загрузить жанры Author.Today: {exc}") from exc
    finally:
        client.close()


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
