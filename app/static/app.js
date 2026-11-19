// Keitaro Wrapper: поведение страниц поверх HTMX. Без inline-скриптов (CSP: script-src 'self').

function toast(message, kind) {
  const box = document.getElementById("toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

// Автодополнение офферов: ищет через /api/offers (по копии каталога Keitaro).
// Если ввести ID, которого нет в каталоге, API вернёт вариант «по ID».
function initOfferSearch(el) {
  if (el.tomselect) return;
  new TomSelect(el, {
    valueField: "id",
    labelField: "label",
    searchField: ["label"],
    maxOptions: 20,
    preload: "focus",
    loadThrottle: 250,
    // список рисуется поверх страницы, иначе его обрезает карточка потока
    dropdownParent: "body",
    load(query, callback) {
      fetch("/api/offers?q=" + encodeURIComponent(query), { credentials: "same-origin" })
        .then((response) => (response.ok ? response.json() : []))
        .then(callback)
        .catch(() => callback());
    },
    render: {
      option(item) {
        const byId = item.label.endsWith("по ID");
        return (
          "<div><span class='opt-id'>#" + escapeHtml(item.id) + "</span>" +
          (byId ? "Добавить по ID" : escapeHtml(item.name)) +
          (byId ? "<span class='opt-hint'>Оффера нет в каталоге ключа — проверим в Keitaro</span>" : "") +
          "</div>"
        );
      },
      item(item) {
        return "<div>" + escapeHtml(item.label) + "</div>";
      },
      no_results: () => "<div class='no-results'>Ничего не найдено. Введите ID оффера</div>",
      loading: () => "<div class='no-results'>Ищем…</div>",
    },
    onChange() {
      updatePreview();
    },
  });
}

function initCountrySelect(el) {
  if (el.tomselect) return;
  new TomSelect(el, {
    plugins: ["remove_button"],
    maxOptions: 300,
    // по коду ищем тоже: «au» находит Австралию
    searchField: ["text", "value"],
    render: {
      no_results: () => "<div class='no-results'>Нет такой страны</div>",
    },
    // после выбора очищаем набранный текст, чтобы сразу искать следующую страну
    onItemAdd() {
      this.setTextboxValue("");
      this.refreshOptions(false);
    },
    onChange() {
      updatePreview();
    },
  });
}

// Полоса распределения: ширина сегментов из data-share (inline style запрещён CSP)
function sizeShareBars(root) {
  root.querySelectorAll(".share-bar [data-share]").forEach((el) => {
    el.style.width = Number(el.dataset.share) + "%";
  });
}

// Предпросмотр на форме создания кампании
function updatePreview() {
  const form = document.getElementById("new-campaign");
  if (!form) return;
  const nameEl = document.getElementById("pv-name");
  const raw = form.querySelector("#name").value.trim();
  const prefix = nameEl.dataset.prefix;
  nameEl.textContent = raw ? (raw.startsWith(prefix) ? raw : prefix + raw) : prefix + "…";

  const countries = Array.from(form.querySelector("#country").selectedOptions).map((o) => o.value);
  document.getElementById("pv-geo").textContent = countries.length ? countries.join(", ") : "не выбраны";

  const offer = form.querySelector("#offer_id");
  const selected = offer.tomselect ? offer.tomselect.getItem(offer.value) : null;
  document.getElementById("pv-offer").textContent = offer.value
    ? (selected ? selected.textContent : "#" + offer.value)
    : "оффер не выбран";
}

htmx.onLoad((root) => {
  root.querySelectorAll("select.offer-search").forEach(initOfferSearch);
  root.querySelectorAll("select.country-select").forEach(initCountrySelect);
  sizeShareBars(root);
  updatePreview();
});

document.addEventListener("input", (event) => {
  if (event.target.id === "name") updatePreview();
});

// Enter в поле выбора страны или оффера не отправляет форму создания:
// Tom Select сам обрабатывает Enter, когда выбирает вариант
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.closest("#new-campaign .ts-wrapper")) {
    event.preventDefault();
  }
});

// Блокируем повторную отправку формы создания
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.id === "new-campaign") {
    form.classList.add("htmx-request");
    form.querySelector("button[type=submit]").disabled = true;
  }
});

// Fetch спрашивает подтверждение, только если есть неопубликованные потоки
document.addEventListener("htmx:confirm", (event) => {
  const question = event.detail.elt.getAttribute("data-confirm-dirty");
  if (!question) return;
  event.preventDefault();
  if (!document.querySelector(".stream.dirty") || window.confirm(question)) {
    event.detail.issueRequest(true);
  }
});

// Открытый попап доли закрывается кликом мимо и по Escape
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.share-edit[open]").forEach((d) => {
    if (!d.contains(event.target)) d.open = false;
  });
  const button = event.target.closest("[data-copy]");
  if (button) {
    navigator.clipboard.writeText(button.dataset.copy).then(
      () => toast("Ссылка скопирована"),
      () => toast("Не удалось скопировать", "bad"),
    );
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    document.querySelectorAll("details.share-edit[open]").forEach((d) => (d.open = false));
  }
});
document.addEventListener("toggle", (event) => {
  const details = event.target;
  if (details.matches && details.matches("details.share-edit") && details.open) {
    const input = details.querySelector("input");
    if (input) { input.focus(); input.select(); }
  }
}, true);

// Пока идёт запрос по потоку, его кнопки заблокированы: Keitaro отвечает не мгновенно,
// а повторный клик ушёл бы уже из перерисованной формы
document.addEventListener("htmx:beforeRequest", (event) => {
  const stream = event.detail.elt.closest(".stream");
  if (!stream) return;
  stream.querySelectorAll("button, input").forEach((el) => {
    if (!el.disabled) { el.disabled = true; el.dataset.busy = "1"; }
  });
});
document.addEventListener("htmx:afterRequest", (event) => {
  // при успехе блок потока заменится целиком; это нужно на случай сбоя сети
  document.querySelectorAll("[data-busy]").forEach((el) => {
    el.disabled = false;
    delete el.dataset.busy;
  });
});

// Ошибки сети и сервера при HTMX-запросах не должны проходить молча
document.addEventListener("htmx:responseError", (event) => {
  const xhr = event.detail.xhr;
  if (xhr.status === 401) return; // HX-Redirect уведёт на вход
  toast("Ошибка " + xhr.status + ": " + (xhr.responseText || "сервер не ответил").slice(0, 200), "bad");
});
document.addEventListener("htmx:sendError", () => toast("Не удалось связаться с сервером", "bad"));
