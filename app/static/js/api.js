// Translations: the page embeds this language's strings (from strings.json) as JSON.
const I18N = JSON.parse(document.getElementById("i18n-data").textContent);

function pluralForm(n) {
  n = Math.abs(Math.trunc(n));
  if (I18N.lang !== "ar") return n === 1 ? "one" : "other";
  if (n === 0) return "zero";
  if (n === 1) return "one";
  if (n === 2) return "two";
  if (n % 100 >= 3 && n % 100 <= 10) return "few";
  if (n % 100 >= 11 && n % 100 <= 99) return "many";
  return "other";
}

// tr("section.key", {name: value}) — same {name} placeholders and plural forms as the server.
function tr(key, params = {}) {
  let value = I18N.strings[key];
  if (value === undefined) return key;
  if (typeof value === "object") value = value[pluralForm(params.n || 0)] || value.other;
  return value.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m));
}

// JSON fetch helper. Every write carries the CSRF header the server requires.
async function apiRequest(method, url, body) {
  const res = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  if (res.status !== 204) {
    try { data = await res.json(); } catch { data = null; }
  }
  return { ok: res.ok, status: res.status, data };
}

// Shared state + helpers for Alpine page components: busy flag, toast, t(), and a request()
// that shows the server's (already translated) message and returns null on failure.
// A component may define onRequestError() to undo optimistic input.
function pageMixin() {
  return {
    busy: false,
    toast: "",
    toastTimer: null,

    t(key, params) {
      return tr(key, params);
    },

    async request(method, url, body) {
      this.busy = true;
      try {
        const res = await apiRequest(method, url, body);
        if (res.status === 401) {
          location.href = "/login";
          return null;
        }
        if (!res.ok) {
          this.notify((res.data && res.data.message) || tr("common.error_generic"));
          if (this.onRequestError) this.onRequestError(res);
          return null;
        }
        return res.data === null ? {} : res.data;
      } catch {
        this.notify(tr("common.error_network"));
        return null;
      } finally {
        this.busy = false;
      }
    },

    notify(message) {
      this.toast = message;
      clearTimeout(this.toastTimer);
      this.toastTimer = setTimeout(() => (this.toast = ""), 4500);
    },
  };
}

async function logout() {
  await apiRequest("POST", "/auth/logout");
  window.location.href = "/login";
}

// Forms marked data-endpoint post their fields as JSON and show the server's message.
function bindJsonForm(form) {
  const alertBox = form.querySelector(".alert-error");
  const submit = form.querySelector("button[type=submit]");

  function showError(message, field) {
    alertBox.textContent = message;
    alertBox.hidden = false;
    form.querySelectorAll("input").forEach((i) => i.removeAttribute("aria-invalid"));
    const input = field && form.querySelector(`[name="${field}"]`);
    if (input) {
      input.setAttribute("aria-invalid", "true");
      input.focus();
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    alertBox.hidden = true;
    submit.disabled = true;
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      const res = await apiRequest("POST", form.dataset.endpoint, body);
      if (res.ok) {
        window.location.href = (res.data && res.data.next) || form.dataset.success || "/";
        return;
      }
      showError((res.data && res.data.message) || tr("common.error_generic"), res.data && res.data.field);
    } catch {
      showError(tr("common.error_network"));
    } finally {
      submit.disabled = false;
    }
  });
}

document.querySelectorAll("form[data-endpoint]").forEach(bindJsonForm);
document.querySelectorAll("[data-logout]").forEach((el) => el.addEventListener("click", logout));
