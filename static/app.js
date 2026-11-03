(() => {
  "use strict";

  const PAGE_SIZE = 35;
  const state = {
    tags: [], genres: [], genresCatalog: [], genresLoaded: false, genresLoadFailed: false,
    results: [], autocompleteId: 0,
    jobId: null, nextLog: 0, page: 1, toastTimer: null,
    pollTimer: null, logs: [], busy: false
  };

  const $ = (selector) => document.querySelector(selector);
  const byId = (id) => document.getElementById(id);
  const tagsHost = byId("tags-groups");
  const genresHost = byId("genres-groups");
  const form = byId("search-form");
  const submitButton = byId("submit-button");
  const cookieInput = byId("cookies-file");
  const cookieName = byId("cookie-file-name");
  const excludeLibrary = byId("exclude-library");
  const finishedOnly = byId("finished-only");
  const cookieArea = byId("cookie-area");
  const formError = byId("form-error");
  const cookieError = byId("cookie-error");
  const progressCard = byId("progress-card");
  const progressTitle = byId("progress-title");
  const progressDescription = byId("progress-description");
  const progressBar = byId("progress-bar");
  const logPreview = byId("log-preview");
  const fullLog = byId("full-log");
  const toggleLog = byId("toggle-log");
  const resultsList = byId("results-list");
  const resultToolbar = byId("result-toolbar");
  const resultCount = byId("result-count");
  const resultsSubtitle = byId("results-subtitle");
  const exportButton = byId("export-button");
  const pagination = byId("pagination");
  const pageInfo = byId("page-info");
  const toast = byId("toast");

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add("visible");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => toast.classList.remove("visible"), 3200);
  }

  function showFormError(message) {
    formError.textContent = message;
    formError.hidden = !message;
  }

  function emptyGroup() {
    return { values: [] };
  }

  function normalizeGroup(group) {
    if (group && Array.isArray(group.values)) return group;
    const normalized = emptyGroup();
    const rawValues = Array.isArray(group) ? group : [];
    rawValues.forEach((value) => addToken(normalized, value));
    return normalized;
  }

  function addToken(group, rawValue) {
    if (!group.values) group.values = [];
    const value = String(rawValue || "").replace(/\s+/g, " ").trim();
    if (!value) return;
    const exists = group.values.some((item) => item.localeCompare(value, "ru", { sensitivity: "base" }) === 0);
    if (!exists) group.values.push(value);
  }

  function renderChips(group) {
    const row = group.entry;
    if (!row) return;
    row.querySelectorAll(".group-chip").forEach((chip) => chip.remove());
    group.values.forEach((value, index) => {
      const chip = document.createElement("span");
      chip.className = "group-chip";
      const text = document.createElement("span");
      text.textContent = value;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Удалить " + value);
      remove.title = "Удалить " + value;
      remove.addEventListener("click", () => {
        group.values.splice(index, 1);
        renderChips(group);
      });
      chip.append(text, remove);
      row.insertBefore(chip, group.inputShell);
    });
  }

  function closeSuggestions(group) {
    if (!group.suggestionList) return;
    group.suggestionList.hidden = true;
    group.input.setAttribute("aria-expanded", "false");
    group.activeIndex = -1;
  }

  function updateActiveSuggestion(group) {
    const options = group.suggestionList.querySelectorAll("[role='option']");
    options.forEach((option, index) => {
      const active = index === group.activeIndex;
      option.setAttribute("aria-selected", String(active));
      if (active) option.scrollIntoView({ block: "nearest" });
    });
  }

  function selectSuggestion(group, value) {
    addToken(group, value);
    group.input.value = "";
    closeSuggestions(group);
    renderChips(group);
    group.input.focus();
  }

  function showSuggestions(group, values, status) {
    const list = group.suggestionList;
    list.replaceChildren();
    group.suggestions = values;
    group.activeIndex = values.length ? 0 : -1;

    if (status) {
      const note = document.createElement("div");
      note.className = "autocomplete-status";
      note.textContent = status;
      list.append(note);
      list.hidden = false;
    } else if (values.length) {
      values.forEach((value, index) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "autocomplete-option";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(index === group.activeIndex));
        option.textContent = value;
        option.addEventListener("mousedown", (event) => {
          // Keep focus on the input so the option can be selected before blur.
          event.preventDefault();
          selectSuggestion(group, value);
        });
        list.append(option);
      });
      list.hidden = false;
    } else {
      list.hidden = true;
    }
    group.input.setAttribute("aria-expanded", String(!list.hidden));
  }

  function genreSuggestions(query) {
    const normalized = query.trim().toLocaleLowerCase("ru");
    return state.genresCatalog
      .map((genre) => genre.name)
      .filter((name) => name.toLocaleLowerCase("ru").includes(normalized))
      .sort((left, right) => {
        const leftStarts = left.toLocaleLowerCase("ru").startsWith(normalized);
        const rightStarts = right.toLocaleLowerCase("ru").startsWith(normalized);
        return Number(rightStarts) - Number(leftStarts) || left.localeCompare(right, "ru");
      })
      .slice(0, 12);
  }

  async function fetchTagSuggestions(group, query, requestId) {
    const controller = new AbortController();
    group.requestController = controller;
    try {
      const response = await fetch("/api/tag-suggestions?q=" + encodeURIComponent(query), {
        headers: { "Accept": "application/json" },
        signal: controller.signal
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Не удалось загрузить теги.");
      if (requestId !== group.requestId || document.activeElement !== group.input) return;
      const suggestions = Array.isArray(payload.tags) ? payload.tags : [];
      showSuggestions(group, suggestions, suggestions.length ? "" : "Подходящие теги не найдены.");
    } catch (error) {
      if (error.name === "AbortError" || requestId !== group.requestId) return;
      showSuggestions(group, [], "Не удалось загрузить локальный каталог тегов. Обнови страницу и попробуй снова.");
    }
  }

  function updateSuggestions(group, kind) {
    const query = group.input.value.trim();
    window.clearTimeout(group.suggestionTimer);
    if (group.requestController) group.requestController.abort();
    group.requestId = (group.requestId || 0) + 1;
    const requestId = group.requestId;

    if (query.length < 2) {
      closeSuggestions(group);
      return;
    }
    if (kind === "genres") {
      if (!state.genresLoaded) {
        showSuggestions(group, [], "Загружаем каталог жанров Author.Today…");
        return;
      }
      if (state.genresLoadFailed) {
        showSuggestions(group, [], "Каталог жанров не загрузился. Обнови страницу и попробуй снова.");
        return;
      }
      const suggestions = genreSuggestions(query);
      showSuggestions(group, suggestions, suggestions.length ? "" : "Совпадений в каталоге жанров нет.");
      return;
    }

    showSuggestions(group, [], "Ищем подходящие теги…");
    group.suggestionTimer = window.setTimeout(
      () => fetchTagSuggestions(group, query, requestId),
      400
    );
  }

  function handleAutocompleteKeydown(event, group) {
    const options = group.suggestionList.querySelectorAll("[role='option']");
    if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      group.activeIndex = Math.min(group.activeIndex + 1, options.length - 1);
      updateActiveSuggestion(group);
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      group.activeIndex = Math.max(group.activeIndex - 1, 0);
      updateActiveSuggestion(group);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (!group.suggestionList.hidden && group.suggestions && group.suggestions.length) {
        const index = group.activeIndex >= 0 ? group.activeIndex : 0;
        selectSuggestion(group, group.suggestions[index]);
      } else if (group.input.value.trim()) {
        showToast("Выбери тег или жанр из предложенных вариантов.");
      }
    } else if (event.key === "Escape") {
      closeSuggestions(group);
    } else if (event.key === ",") {
      // A comma is part of a possible multi-word value, not a separator.
      event.preventDefault();
    }
  }

  function createGroup(host, kind, group, index) {
    if (!Array.isArray(group.values)) group.values = [];
    const wrapper = document.createElement("div");
    wrapper.className = "group-row";

    const top = document.createElement("div");
    top.className = "group-topline";
    const label = document.createElement("span");
    label.className = "group-name";
    label.textContent = (kind === "tags" ? "Группа тегов " : "Группа жанров ") + (index + 1);
    const removeGroup = document.createElement("button");
    removeGroup.className = "remove-group";
    removeGroup.type = "button";
    removeGroup.textContent = "×";
    removeGroup.setAttribute("aria-label", "Удалить группу " + (index + 1));
    removeGroup.title = "Удалить группу";
    removeGroup.disabled = state[kind].length <= 1;
    removeGroup.addEventListener("click", () => {
      if (state[kind].length <= 1) return;
      const position = state[kind].indexOf(group);
      if (position >= 0) state[kind].splice(position, 1);
      renderGroups(kind);
    });
    top.append(label, removeGroup);

    const entry = document.createElement("div");
    entry.className = "group-entry-line";
    group.entry = entry;

    const inputShell = document.createElement("div");
    inputShell.className = "autocomplete-shell";
    group.inputShell = inputShell;

    const input = document.createElement("input");
    input.className = "group-input";
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = kind === "tags" ? "Начни вводить тег…" : "Начни вводить жанр…";
    input.setAttribute("aria-label", kind === "tags" ? "Поиск тегов, группа " + (index + 1) : "Поиск жанров, группа " + (index + 1));
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.id = "autocomplete-input-" + (++state.autocompleteId);

    const suggestionList = document.createElement("div");
    suggestionList.className = "autocomplete-list";
    suggestionList.id = input.id + "-list";
    suggestionList.setAttribute("role", "listbox");
    suggestionList.hidden = true;
    input.setAttribute("aria-controls", suggestionList.id);

    group.input = input;
    group.suggestionList = suggestionList;
    group.suggestions = [];
    group.activeIndex = -1;
    input.addEventListener("input", () => updateSuggestions(group, kind));
    input.addEventListener("keydown", (event) => handleAutocompleteKeydown(event, group));
    input.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (document.activeElement !== input) closeSuggestions(group);
      }, 120);
    });
    inputShell.append(input, suggestionList);
    entry.append(inputShell);

    wrapper.append(top, entry);
    if (group.values.length) {
      const or = document.createElement("span");
      or.className = "group-or-label";
      or.textContent = group.values.length > 1 ? "ИЛИ" : " ";
      wrapper.append(or);
    }
    host.append(wrapper);
    renderChips(group);
  }

  function renderGroups(kind) {
    const host = kind === "tags" ? tagsHost : genresHost;
    if (!host) return;
    state[kind] = (Array.isArray(state[kind]) ? state[kind] : []).map(normalizeGroup);
    if (!state[kind].length) state[kind].push(emptyGroup());
    host.replaceChildren();
    state[kind].forEach((group, index) => createGroup(host, kind, group, index));
  }

  function addGroup(kind, values) {
    const group = emptyGroup();
    (values || []).forEach((value) => addToken(group, value));
    if (!Array.isArray(state[kind])) state[kind] = [];
    state[kind].push(group);
    renderGroups(kind);
    const input = state[kind][state[kind].length - 1].input;
    if (input) input.focus();
  }

  function flushPendingInputs() {
    for (const kind of ["tags", "genres"]) {
      const pending = state[kind].find((group) => group.input && group.input.value.trim());
      if (pending) {
        showFormError("Выбери " + (kind === "tags" ? "тег" : "жанр") + " из подсказок или очисти поле.");
        pending.input.focus();
        updateSuggestions(pending, kind);
        return false;
      }
    }
    return true;
  }

  function serializeGroups(kind) {
    return state[kind]
      .map((group) => group.values.map((value) => value.trim()).filter(Boolean).join(", "))
      .filter(Boolean)
      .join("\n");
  }

  function loadGroupsFromText(kind, value) {
    const lines = String(value || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!lines.length) {
      state[kind] = [emptyGroup()];
    } else {
      state[kind] = lines.map((line) => {
        const group = emptyGroup();
        line.split(",").forEach((part) => addToken(group, part));
        return group;
      });
    }
    renderGroups(kind);
  }

  async function loadGenres() {
    try {
      const response = await fetch("/api/genres", {
        headers: { "Accept": "application/json" }
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Не удалось получить список жанров.");
      if (!Array.isArray(payload.genres) || payload.genres.length === 0) {
        throw new Error("Каталог жанров пуст. Проверь файл static/genres.json.");
      }
      state.genresCatalog = payload.genres.filter((genre) => genre && genre.name);
      state.genresLoadFailed = false;
    } catch (error) {
      state.genresLoadFailed = true;
      showToast(error.message || "Не удалось загрузить каталог жанров Author.Today.");
    } finally {
      state.genresLoaded = true;
      state.genres.forEach((group) => {
        if (group.input && group.input.value.trim() && document.activeElement === group.input) {
          updateSuggestions(group, "genres");
        }
      });
    }
  }

  function updateCookieVisibility() {
    cookieArea.hidden = !excludeLibrary.checked;
    if (!excludeLibrary.checked) cookieError.hidden = true;
  }

  function setBusy(busy) {
    state.busy = busy;
    submitButton.disabled = busy;
    submitButton.querySelector("span:first-child").textContent = busy ? "Ищем произведения…" : "Найти произведения";
    const spinner = document.querySelector(".progress-spinner");
    if (spinner) spinner.style.display = busy ? "" : "none";
    progressBar.style.animationPlayState = busy ? "running" : "paused";
  }

  function createEmptyState() {
    const wrapper = document.createElement("div");
    wrapper.className = "empty-state";
    wrapper.innerHTML = '<div class="empty-illustration" aria-hidden="true"><div class="empty-page page-one"></div><div class="empty-page page-two"></div><div class="empty-spark">✦</div></div><h3>Здесь начнётся поиск</h3><p>Добавь жанры, теги или ключевые слова из описания. Результаты появятся здесь.</p>';
    return wrapper;
  }

  function clearResults() {
    state.results = [];
    state.page = 1;
    resultsList.replaceChildren(createEmptyState());
    resultToolbar.hidden = true;
    pagination.hidden = true;
    exportButton.disabled = true;
    resultsSubtitle.textContent = "Задай фильтры и запусти поиск";
    resultCount.textContent = "0";
  }

  function updateUrl() {
    const params = new URLSearchParams();
    const tags = serializeGroups("tags");
    const genres = serializeGroups("genres");
    const description = byId("description").value.trim();
    if (tags) params.set("tags", tags);
    if (genres) params.set("genres", genres);
    if (description) params.set("description", description);
    const query = params.toString();
    history.replaceState(null, "", query ? "/?" + query : "/");
  }

  function getFilteredResults() {
    const query = byId("result-filter").value.trim().toLocaleLowerCase("ru");
    const sort = byId("result-sort").value;
    const filtered = state.results.filter((work) => {
      const haystack = ((work.title || "") + " " + (work.author || "")).toLocaleLowerCase("ru");
      return !query || haystack.includes(query);
    });
    filtered.sort((a, b) => {
      const left = String(sort === "author" ? a.author || "" : a.title || "").toLocaleLowerCase("ru");
      const right = String(sort === "author" ? b.author || "" : b.title || "").toLocaleLowerCase("ru");
      return left.localeCompare(right, "ru");
    });
    return filtered;
  }

  function createPill(text, kind) {
    const pill = document.createElement("span");
    pill.className = "meta-pill" + (kind ? " " + kind : "");
    pill.textContent = text;
    return pill;
  }

  function renderResults() {
    const filtered = getFilteredResults();
    const total = filtered.length;
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    state.page = Math.min(state.page, pageCount);
    const start = (state.page - 1) * PAGE_SIZE;
    const pageItems = filtered.slice(start, start + PAGE_SIZE);

    resultToolbar.hidden = false;
    resultCount.textContent = new Intl.NumberFormat("ru-RU").format(total);
    resultsSubtitle.textContent = total === state.results.length
      ? "Результаты собраны по заданным фильтрам"
      : "Показано " + total + " из " + state.results.length + " произведений после фильтрации";
    exportButton.disabled = state.results.length === 0;
    resultsList.replaceChildren();

    if (total === 0) {
      const empty = document.createElement("div");
      empty.className = "no-results";
      const strong = document.createElement("strong");
      strong.textContent = state.results.length ? "Ничего не найдено по этому фильтру" : "Подходящих произведений пока нет";
      const note = document.createElement("span");
      note.textContent = state.results.length ? "Измени слова в поле фильтра результатов." : "Попробуй убрать одну из групп или изменить условия.";
      empty.append(strong, note);
      resultsList.append(empty);
    } else {
      pageItems.forEach((work) => {
        const card = document.createElement("article");
        card.className = "work-card";
        const main = document.createElement("div");
        main.className = "work-card-main";
        const heading = document.createElement("h3");
        heading.className = "work-card-title";
        const titleLink = document.createElement("a");
        titleLink.href = work.url || ("https://author.today/work/" + encodeURIComponent(work.id));
        titleLink.target = "_blank";
        titleLink.rel = "noopener noreferrer";
        titleLink.textContent = work.title || ("Произведение " + work.id);
        heading.append(titleLink);

        const author = document.createElement("div");
        author.className = "work-card-author";
        author.textContent = work.author || "Автор не указан";
        const meta = document.createElement("div");
        meta.className = "work-card-meta";
        (work.genres || []).slice(0, 8).forEach((genre) => meta.append(createPill(genre, "genre")));
        (work.tags || []).slice(0, 8).forEach((tag) => meta.append(createPill(tag, "")));
        if (work.series_name) {
          const seriesText = work.series_name + (work.series_number ? " · #" + work.series_number : "");
          meta.append(createPill(seriesText, "series"));
        }
        main.append(heading, author);
        if (meta.childElementCount) main.append(meta);
        if (work.description) {
          const desc = document.createElement("p");
          desc.className = "work-card-description";
          desc.textContent = work.description.length > 200 ? work.description.slice(0, 197) + "…" : work.description;
          main.append(desc);
        }

        const open = document.createElement("a");
        open.className = "work-card-open";
        open.href = titleLink.href;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        open.textContent = "↗";
        open.setAttribute("aria-label", "Открыть " + (work.title || "произведение") + " на Author.Today");
        card.append(main, open);
        resultsList.append(card);
      });
    }

    pagination.hidden = total <= PAGE_SIZE;
    pageInfo.textContent = "Страница " + state.page + " из " + pageCount;
    byId("prev-page").disabled = state.page <= 1;
    byId("next-page").disabled = state.page >= pageCount;
  }

  function appendLogs(messages) {
    if (!Array.isArray(messages) || !messages.length) return;
    state.logs.push(...messages);
    logPreview.textContent = messages[messages.length - 1];
    fullLog.textContent = state.logs.join("\n");
    fullLog.scrollTop = fullLog.scrollHeight;
  }

  async function pollJob(jobId) {
    if (state.jobId !== jobId) return;
    try {
      const response = await fetch("/api/jobs/" + encodeURIComponent(jobId) + "?after=" + state.nextLog, {
        headers: { "Accept": "application/json" }
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Не удалось получить состояние поиска.");
      appendLogs(payload.logs);
      state.nextLog = payload.next_log ?? state.nextLog;

      if (payload.status === "completed") {
        state.results = Array.isArray(payload.results) ? payload.results : [];
        state.page = 1;
        renderResults();
        progressTitle.textContent = "Поиск завершён";
        progressDescription.textContent = "Найдено произведений: " + state.results.length;
        byId("progress-percent").textContent = "ГОТОВО";
        const spinner = document.querySelector(".progress-spinner");
        if (spinner) spinner.style.display = "none";
        progressBar.style.animation = "none";
        progressBar.style.width = "100%";
        setBusy(false);
        showToast("Готово: найдено " + state.results.length + " произведений");
        return;
      }
      if (payload.status === "failed") {
        progressTitle.textContent = "Поиск завершился с ошибкой";
        progressDescription.textContent = payload.error || "Проверь настройки и повтори попытку.";
        setBusy(false);
        showFormError(payload.error || "Не удалось выполнить поиск.");
        showToast("Поиск завершился с ошибкой");
        return;
      }
      if (payload.status === "queued") {
        progressTitle.textContent = "Ожидаем запуска…";
        progressDescription.textContent = "Задание подготовлено.";
      } else {
        progressTitle.textContent = "Собираем произведения…";
        progressDescription.textContent = "Обрабатываем запросы Author.Today";
      }
      state.pollTimer = window.setTimeout(() => pollJob(jobId), 850);
    } catch (error) {
      progressTitle.textContent = "Потеряна связь с поиском";
      progressDescription.textContent = error.message || "Повтори запуск поиска.";
      setBusy(false);
      showFormError(error.message || "Не удалось получить состояние поиска.");
    }
  }

  function validateForm() {
    showFormError("");
    if (!flushPendingInputs()) return false;
    cookieError.hidden = true;
    if (!serializeGroups("tags") && !serializeGroups("genres") && !byId("description").value.trim()) {
      showFormError("Добавь хотя бы один тег, жанр или слово для поиска.");
      return false;
    }
    if (excludeLibrary.checked && !cookieInput.files.length) {
      cookieError.textContent = "Прикрепи cookies.txt, чтобы загрузить списки «Прочитано» и «Не интересно».";
      cookieError.hidden = false;
      cookieInput.focus();
      return false;
    }
    if (cookieInput.files.length && !cookieInput.files[0].name.toLowerCase().endsWith(".txt")) {
      cookieError.textContent = "Выбери файл cookies.txt в формате Netscape/curl.";
      cookieError.hidden = false;
      cookieInput.focus();
      return false;
    }
    return true;
  }

  async function startSearch(event) {
    event.preventDefault();
    if (state.busy || !validateForm()) return;
    updateUrl();
    const formData = new FormData();
    formData.append("username", byId("username").value.trim());
    formData.append("tags", serializeGroups("tags"));
    formData.append("genres", serializeGroups("genres"));
    formData.append("description", byId("description").value.trim());
    formData.append("exclude_library", String(excludeLibrary.checked));
    formData.append("collapse_cycles", String(byId("collapse-cycles").checked));
    formData.append("only_finished", String(finishedOnly.checked));
    formData.append("max_pages", byId("max-pages").value || "200");
    formData.append("delay", byId("delay").value || "0.3");
    if (cookieInput.files[0]) formData.append("cookies", cookieInput.files[0]);

    clearResults();
    showFormError("");
    state.logs = [];
    state.nextLog = 0;
    state.jobId = null;
    logPreview.textContent = "Подключаемся к Author.Today…";
    fullLog.textContent = "";
    progressTitle.textContent = "Подготавливаем поиск…";
    progressDescription.textContent = "Собираем заданные фильтры";
    progressBar.style.animation = "";
    progressBar.style.width = "";
    const spinner = document.querySelector(".progress-spinner");
    if (spinner) spinner.style.display = "";
    progressCard.hidden = false;
    toggleLog.setAttribute("aria-expanded", "false");
    toggleLog.innerHTML = 'Показать ход поиска <span aria-hidden="true">⌄</span>';
    fullLog.hidden = true;
    setBusy(true);

    try {
      const response = await fetch("/api/search", { method: "POST", body: formData });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Не удалось запустить поиск.");
      state.jobId = payload.job_id;
      state.pollTimer = window.setTimeout(() => pollJob(payload.job_id), 400);
    } catch (error) {
      setBusy(false);
      progressTitle.textContent = "Не удалось запустить поиск";
      progressDescription.textContent = error.message || "Проверь подключение и настройки.";
      showFormError(error.message || "Ошибка запуска поиска.");
      showToast("Не удалось запустить поиск");
    }
  }

  function exportCsv() {
    if (!state.results.length) return;
    const rows = [
      ["Название", "Автор", "Жанры", "Теги", "Цикл", "Номер части", "Описание", "Ссылка"],
      ...state.results.map((work) => [
        work.title || "", work.author || "", (work.genres || []).join(", "),
        (work.tags || []).join(", "), work.series_name || "", work.series_number || "",
        work.description || "", work.url || ""
      ])
    ];
    const csv = "\uFEFF" + rows.map((row) => row.map((cell) => {
      const text = String(cell ?? "");
      return '"' + text.replace(/"/g, '""') + '"';
    }).join(";")).join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "author_today_search.csv";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("CSV-файл сохранён");
  }

  document.querySelectorAll("[data-add-group]").forEach((button) => {
    button.addEventListener("click", () => addGroup(button.dataset.addGroup));
  });
  form.addEventListener("submit", startSearch);
  excludeLibrary.addEventListener("change", updateCookieVisibility);
  cookieInput.addEventListener("change", () => {
    const file = cookieInput.files[0];
    cookieName.textContent = file ? file.name : "Добавить cookies.txt";
    cookieError.hidden = true;
  });
  exportButton.addEventListener("click", exportCsv);
  byId("clear-button").addEventListener("click", () => {
    if (state.busy) {
      showToast("Сначала дождись завершения текущего поиска");
      return;
    }
    clearResults();
    progressCard.hidden = true;
    showFormError("");
  });
  toggleLog.addEventListener("click", () => {
    const expanded = toggleLog.getAttribute("aria-expanded") === "true";
    toggleLog.setAttribute("aria-expanded", String(!expanded));
    fullLog.hidden = expanded;
    toggleLog.innerHTML = expanded
      ? 'Показать ход поиска <span aria-hidden="true">⌄</span>'
      : 'Скрыть ход поиска <span aria-hidden="true">⌃</span>';
  });
  byId("result-filter").addEventListener("input", () => {
    state.page = 1;
    renderResults();
  });
  byId("result-sort").addEventListener("change", renderResults);
  byId("prev-page").addEventListener("click", () => {
    if (state.page > 1) { state.page -= 1; renderResults(); }
  });
  byId("next-page").addEventListener("click", () => {
    state.page += 1;
    renderResults();
  });

  const params = new URLSearchParams(location.search);
  if (params.has("tags")) loadGroupsFromText("tags", params.get("tags"));
  if (params.has("genres")) loadGroupsFromText("genres", params.get("genres"));
  if (params.has("description")) byId("description").value = params.get("description");

  try {
    renderGroups("tags");
    renderGroups("genres");
  } catch (error) {
    console.error("Не удалось отрисовать группы фильтров", error);
  }
  updateCookieVisibility();
  loadGenres();
})();
