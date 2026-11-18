// Автодополнение офферов: ищет через наше API (/api/offers), а оно — по копии каталога Keitaro
function initOfferSearch(el) {
  if (el.tomselect) return;
  new TomSelect(el, {
    valueField: "id",
    labelField: "label",
    searchField: ["label"],
    maxOptions: 20,
    preload: "focus",
    loadThrottle: 250,
    load(query, callback) {
      fetch("/api/offers?q=" + encodeURIComponent(query))
        .then((response) => response.json())
        .then(callback)
        .catch(() => callback());
    },
    render: {
      no_results: () => '<div class="no-results">Ничего не найдено</div>',
    },
  });
}

function initCountrySelect(el) {
  if (el.tomselect) return;
  new TomSelect(el, {
    plugins: ["remove_button"],
    maxOptions: 300,
    // по коду ищем тоже: ввод «au» находит Австралию
    searchField: ["text", "value"],
  });
}

// htmx.onLoad срабатывает и при загрузке страницы, и для каждого подставленного фрагмента
htmx.onLoad((root) => {
  root.querySelectorAll("select.offer-search").forEach(initOfferSearch);
  root.querySelectorAll("select.country-select").forEach(initCountrySelect);
});

// Fetch спрашивает подтверждение, только если есть неопубликованные потоки
document.addEventListener("htmx:confirm", (event) => {
  const el = event.detail.elt;
  const question = el.getAttribute("data-confirm-dirty");
  if (!question) return;
  event.preventDefault();
  if (!document.querySelector(".stream.dirty") || window.confirm(question)) {
    event.detail.issueRequest(true);
  }
});

// Кнопка «Копировать» рядом со ссылкой кампании
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  navigator.clipboard.writeText(button.dataset.copy).then(() => {
    button.textContent = "Скопировано";
  });
});

// Ошибки сети и сервера при HTMX-запросах не должны проходить молча
document.addEventListener("htmx:responseError", (event) => {
  window.alert("Ошибка " + event.detail.xhr.status + ": " + (event.detail.xhr.responseText || "сервер не ответил"));
});
document.addEventListener("htmx:sendError", () => {
  window.alert("Не удалось связаться с сервером");
});
