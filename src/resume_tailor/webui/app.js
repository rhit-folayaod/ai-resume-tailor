/* Browser UI for resume-tailor.
 *
 * Plain DOM, no build step, no framework. The editor keeps the whole store in
 * `state.store` and re-renders only on structural changes (adding or removing
 * things); plain typing mutates state directly so focus and cursor position
 * survive.
 */

const SESSION_KEY = "resume_tailor_session";
const PKCE_VERIFIER_KEY = "resume_tailor_pkce_verifier";

const state = {
  store: null,
  pristine: "",
  jdText: "",
  jdSource: "",
  runId: null,
  authRequired: false,
  supabaseUrl: "",
  supabaseAnonKey: "",
  adminLoginEnabled: false,
  session: null,
  userEmail: "",
};

/* Helpers ------------------------------------------------------------------ */

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child) node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function toast(message, kind = "") {
  const node = el("div", { class: `toast ${kind}`, text: message });
  $("toasts").appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? 12000 : 5000);
}

function authHeaders() {
  if (!state.session || !state.session.access_token) return {};
  return { Authorization: `Bearer ${state.session.access_token}` };
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), ...authHeaders() };
  const response = await fetch(path, { ...options, headers });
  const isJson = (response.headers.get("content-type") || "").includes("json");
  const body = isJson ? await response.json() : null;
  if (!response.ok) {
    // Only treat /api/me as a hard sign-out. Other 401s (store/DB blips, stale
    // optional auth) must not bounce a just-authenticated user back to login.
    if (response.status === 401 && state.authRequired && path === "/api/me") {
      clearSession();
      showLogin();
    }
    throw new Error((body && body.error) || `${response.status} ${response.statusText}`);
  }
  return body;
}

function busy(button, label) {
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = "";
  button.append(el("span", { class: "spinner" }), label);
  return () => {
    button.disabled = false;
    button.innerHTML = original;
  };
}

/* Auth (hosted mode) ------------------------------------------------------- */

function loadSession() {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    state.session = raw ? JSON.parse(raw) : null;
  } catch {
    state.session = null;
  }
}

function saveSession(session) {
  state.session = session;
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

function clearSession() {
  state.session = null;
  state.userEmail = "";
  localStorage.removeItem(SESSION_KEY);
}

function showLogin() {
  $("login-gate").classList.remove("is-hidden");
  $("app-shell").classList.add("is-hidden");
  $("user-chip").classList.add("is-hidden");
}

function showApp() {
  $("login-gate").classList.add("is-hidden");
  $("app-shell").classList.remove("is-hidden");
  if (state.userEmail) {
    $("user-email").textContent = state.userEmail;
    $("user-chip").classList.remove("is-hidden");
  } else {
    $("user-chip").classList.add("is-hidden");
  }
}

function base64UrlEncode(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function randomVerifier() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function challengeFromVerifier(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64UrlEncode(digest);
}

async function exchangeAuthCode(code) {
  const verifier = localStorage.getItem(PKCE_VERIFIER_KEY);
  if (!verifier) {
    throw new Error(
      "This login link must be opened in the same browser where you clicked “Send magic link”. Request a new link and open it here.",
    );
  }
  const response = await fetch(`${state.supabaseUrl}/auth/v1/token?grant_type=pkce`, {
    method: "POST",
    headers: {
      apikey: state.supabaseAnonKey,
      Authorization: `Bearer ${state.supabaseAnonKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ auth_code: code, code_verifier: verifier }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      (body && (body.error_description || body.msg || body.error)) ||
        "could not finish sign-in from the magic link",
    );
  }
  localStorage.removeItem(PKCE_VERIFIER_KEY);
  saveSession({
    access_token: body.access_token,
    refresh_token: body.refresh_token || "",
    expires_at: Date.now() + Number(body.expires_in || 3600) * 1000,
  });
}

async function consumeAuthRedirect() {
  // Supabase PKCE redirects with ?code=...; older/implicit flow uses #access_token=...
  const hash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const query = window.location.search.startsWith("?") ? window.location.search.slice(1) : "";
  if (!hash && !query) return false;

  const hashParams = new URLSearchParams(hash);
  const queryParams = new URLSearchParams(query);
  const error =
    hashParams.get("error_description") ||
    hashParams.get("error") ||
    queryParams.get("error_description") ||
    queryParams.get("error");
  if (error) {
    history.replaceState(null, "", window.location.pathname);
    toast(decodeURIComponent(error.replace(/\+/g, " ")), "error");
    return false;
  }

  const code = queryParams.get("code");
  if (code) {
    history.replaceState(null, "", window.location.pathname);
    await exchangeAuthCode(code);
    return true;
  }

  // Some email clients land on ?token_hash=...&type=email (or magiclink).
  const tokenHash = queryParams.get("token_hash");
  const otpType = queryParams.get("type");
  if (tokenHash && otpType) {
    history.replaceState(null, "", window.location.pathname);
    await verifyTokenHash(tokenHash, otpType);
    return true;
  }

  const access = hashParams.get("access_token") || queryParams.get("access_token");
  const refresh = hashParams.get("refresh_token") || queryParams.get("refresh_token") || "";
  const expires = hashParams.get("expires_in") || queryParams.get("expires_in") || "3600";
  if (!access) return false;
  saveSession({
    access_token: access,
    refresh_token: refresh,
    expires_at: Date.now() + Number(expires) * 1000,
  });
  history.replaceState(null, "", window.location.pathname);
  return true;
}

async function verifyTokenHash(tokenHash, type) {
  const response = await fetch(`${state.supabaseUrl}/auth/v1/verify`, {
    method: "POST",
    headers: {
      apikey: state.supabaseAnonKey,
      Authorization: `Bearer ${state.supabaseAnonKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ token_hash: tokenHash, type }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      (body && (body.error_description || body.msg || body.error)) ||
        "could not finish sign-in from the magic link",
    );
  }
  saveSession({
    access_token: body.access_token,
    refresh_token: body.refresh_token || "",
    expires_at: Date.now() + Number(body.expires_in || 3600) * 1000,
  });
}

async function sendMagicLink(email) {
  const verifier = randomVerifier();
  const challenge = await challengeFromVerifier(verifier);
  localStorage.setItem(PKCE_VERIFIER_KEY, verifier);

  // redirect_to must be a query param (Supabase Auth / auth-js), not a body field.
  const redirectTo = encodeURIComponent(`${window.location.origin}/`);
  const response = await fetch(
    `${state.supabaseUrl}/auth/v1/otp?redirect_to=${redirectTo}`,
    {
      method: "POST",
      headers: {
        apikey: state.supabaseAnonKey,
        Authorization: `Bearer ${state.supabaseAnonKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email,
        create_user: true,
        code_challenge: challenge,
        code_challenge_method: "s256",
      }),
    },
  );
  if (!response.ok) {
    localStorage.removeItem(PKCE_VERIFIER_KEY);
    const body = await response.json().catch(() => null);
    const code = (body && body.error_code) || "";
    if (code === "over_email_send_rate_limit" || response.status === 429) {
      throw new Error(
        "Supabase refused to send: email rate limit exceeded. Wait an hour, or " +
          "configure custom SMTP in the Supabase dashboard (Authentication → Emails → SMTP). " +
          "The built-in sender allows only a couple of emails per hour and delivers " +
          "solely to Supabase team-member addresses.",
      );
    }
    throw new Error(
      (body && (body.error_description || body.msg || body.error)) || "could not send magic link",
    );
  }
}

async function refreshSessionIfNeeded() {
  if (!state.session || !state.session.refresh_token) return;
  if (state.session.expires_at && Date.now() < state.session.expires_at - 60_000) return;
  const response = await fetch(
    `${state.supabaseUrl}/auth/v1/token?grant_type=refresh_token`,
    {
      method: "POST",
      headers: {
        apikey: state.supabaseAnonKey,
        Authorization: `Bearer ${state.supabaseAnonKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ refresh_token: state.session.refresh_token }),
    },
  );
  if (!response.ok) {
    clearSession();
    return;
  }
  const body = await response.json();
  saveSession({
    access_token: body.access_token,
    refresh_token: body.refresh_token || state.session.refresh_token,
    expires_at: Date.now() + Number(body.expires_in || 3600) * 1000,
  });
}

$("login-button").addEventListener("click", async () => {
  const email = $("login-email").value.trim();
  if (!email) return;
  const done = busy($("login-button"), "Sending");
  try {
    await sendMagicLink(email);
    $("login-sent").classList.remove("is-hidden");
    toast("Magic link sent.", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    done();
  }
});

$("login-email").addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("login-button").click();
});

async function adminPasswordLogin(password) {
  const response = await fetch("/api/admin-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((body && body.error) || "admin sign-in failed");
  }
  saveSession({
    access_token: body.access_token,
    refresh_token: body.refresh_token || "",
    expires_at: Date.now() + Number(body.expires_in || 3600) * 1000,
  });
  state.userEmail = (body.user && body.user.email) || "admin@resume-tailor.local";
}

$("admin-login-button").addEventListener("click", async () => {
  const password = $("admin-password").value;
  if (!password) return;
  const done = busy($("admin-login-button"), "Signing in");
  try {
    await adminPasswordLogin(password);
    showApp();
    await loadHealth();
    await loadStore();
    toast("Signed in as admin.", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    done();
  }
});

$("admin-password").addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("admin-login-button").click();
});

$("logout-button").addEventListener("click", () => {
  clearSession();
  showLogin();
  toast("Signed out.");
});

/* Tabs --------------------------------------------------------------------- */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((other) => other.classList.remove("is-active"));
    document.querySelectorAll(".panel").forEach((panel) => panel.classList.remove("is-active"));
    tab.classList.add("is-active");
    $(`panel-${tab.dataset.tab}`).classList.add("is-active");
  });
});

/* Health ------------------------------------------------------------------- */

async function loadHealth() {
  try {
    const health = await api("/api/health");
    const pills = [
      health.model_configured
        ? { text: health.model, kind: "ok" }
        : { text: "no OPENAI_API_KEY", kind: "bad" },
      health.tectonic
        ? { text: "tectonic ready", kind: "ok" }
        : { text: "tectonic missing", kind: "bad" },
    ];
    if (health.auth_required) {
      pills.push(
        health.user
          ? { text: "signed in", kind: "ok" }
          : { text: "sign in required", kind: "bad" },
      );
    }
    $("status").replaceChildren(
      ...pills.map((pill) => el("span", { class: `pill ${pill.kind}`, text: pill.text })),
    );
    if (health.store_path) {
      $("store-path").textContent = health.store_path;
    } else if (health.user) {
      $("store-path").textContent = `hosted store for ${health.user.email}`;
    } else {
      $("store-path").textContent = "hosted mode";
    }
    if (health.user) state.userEmail = health.user.email;
  } catch (error) {
    toast(error.message, "error");
  }
}

/* Posting source ----------------------------------------------------------- */

document.querySelectorAll(".segment").forEach((segment) => {
  segment.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach((other) => other.classList.remove("is-active"));
    segment.classList.add("is-active");
    for (const mode of ["url", "file", "paste"]) {
      $(`source-${mode}`).classList.toggle("is-hidden", mode !== segment.dataset.mode);
    }
  });
});

function setPosting(text, source) {
  state.jdText = text;
  state.jdSource = source;
  $("posting").classList.toggle("is-hidden", !text);
  $("posting-source").textContent = text ? `${source} — ${text.length} characters` : "";
  $("posting-text").textContent = text;
  $("tailor-button").disabled = !text;
}

$("posting-toggle").addEventListener("click", () => {
  const hidden = $("posting-text").classList.toggle("is-hidden");
  $("posting-toggle").textContent = hidden ? "show text" : "hide text";
});

$("fetch-url").addEventListener("click", async () => {
  const url = $("url-input").value.trim();
  if (!url) return;
  const done = busy($("fetch-url"), "Fetching");
  try {
    const form = new FormData();
    form.append("url", url);
    const result = await api("/api/ingest", { method: "POST", body: form });
    setPosting(result.text, result.source);
    toast("Posting loaded.", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    done();
  }
});

$("url-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("fetch-url").click();
});

async function uploadFile(file) {
  if (!file) return;
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const result = await api("/api/ingest", { method: "POST", body: form });
    setPosting(result.text, result.source);
    toast("Posting loaded.", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

$("file-input").addEventListener("change", (event) => uploadFile(event.target.files[0]));

const dropzone = $("dropzone");
["dragenter", "dragover"].forEach((name) =>
  document.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add("is-over");
  }),
);
["dragleave", "drop"].forEach((name) =>
  document.addEventListener(name, (event) => {
    event.preventDefault();
    if (name === "drop" || event.relatedTarget === null) dropzone.classList.remove("is-over");
  }),
);
document.addEventListener("drop", (event) => {
  const file = event.dataTransfer && event.dataTransfer.files[0];
  if (!file) return;
  // A file can be dropped anywhere on the page; switch to the file tab so the
  // result is visible rather than hidden behind another mode.
  document.querySelector('.segment[data-mode="file"]').click();
  uploadFile(file);
});

$("paste-input").addEventListener("input", (event) =>
  setPosting(event.target.value.trim(), "pasted text"),
);

/* Tailoring ---------------------------------------------------------------- */

$("tailor-button").addEventListener("click", async () => {
  const done = busy($("tailor-button"), "Ranking");
  try {
    const result = await api("/api/tailor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jd_text: state.jdText,
        max_bullets: Number($("max-bullets").value),
        max_projects: Number($("max-projects").value),
        max_bullets_per_project: Number($("max-per-project").value),
        llm_rank: $("llm-rank").checked,
        reorder_skills: $("reorder-skills").checked,
      }),
    });
    renderSelection(result);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    done();
  }
});

function renderSelection(result) {
  state.runId = result.run_id;
  const { job, selection } = result;

  $("job-summary").replaceChildren(
    el("div", { class: "job-line" }, [
      el("h3", { text: `${job.role_flavor} · ${job.seniority}` }),
      el("div", { class: "chips" }, [
        ...job.required_skills.map((skill) => el("span", { class: "chip required", text: skill })),
        ...job.preferred_skills.map((skill) => el("span", { class: "chip", text: skill })),
      ]),
    ]),
  );

  const entries = selection.projects.map((project) =>
    el("div", { class: "entry" }, [
      el("div", { class: "entry-head" }, [
        el("strong", { text: project.name }),
        el("span", { class: "score", text: project.score.toFixed(2) }),
      ]),
      el("div", {
        class: "entry-meta",
        text: [project.organization, project.dates].filter(Boolean).join(" · "),
      }),
      el(
        "ul",
        {},
        project.bullets.map((bullet) =>
          el("li", {}, [
            bullet.text,
            bullet.matched.length
              ? el("div", { class: "matched", text: `matched: ${bullet.matched.join(", ")}` })
              : null,
          ]),
        ),
      ),
    ]),
  );

  const skipped = selection.skipped.length
    ? el("div", { class: "skipped" }, [
        el("strong", { text: "Not selected" }),
        ...selection.skipped.map((item) =>
          el("div", { text: `${item.name} — ${item.reason} (${item.score.toFixed(2)})` }),
        ),
      ])
    : null;

  const heading = `${selection.bullet_count} bullets from ${selection.projects.length} entries${
    selection.reranked_by_llm ? ", model-reranked" : ""
  }`;

  $("selection").replaceChildren(el("p", { class: "hint", text: heading }), ...entries, skipped);

  $("results").classList.remove("is-hidden");
  $("results-empty").classList.add("is-hidden");
  $("preview").classList.add("is-hidden");
  $("download-pdf").classList.add("is-hidden");
  $("download-tex").classList.remove("is-hidden");
  $("download-tex").onclick = async (event) => {
    event.preventDefault();
    try {
      const response = await fetch(`/api/resume/${state.runId}/tex`, {
        headers: authHeaders(),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error((body && body.error) || "could not fetch .tex");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "resume.tex";
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      toast(error.message, "error");
    }
  };
}

$("build-pdf").addEventListener("click", async () => {
  if (!state.runId) return;
  const done = busy($("build-pdf"), "Compiling");
  try {
    const response = await fetch(`/api/resume/${state.runId}`, { headers: authHeaders() });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      if (response.status === 401 && state.authRequired) {
        clearSession();
        showLogin();
      }
      throw new Error((body && body.error) || "compilation failed");
    }
    const url = URL.createObjectURL(await response.blob());
    $("preview").src = url;
    $("preview").classList.remove("is-hidden");
    $("download-pdf").href = url;
    $("download-pdf").classList.remove("is-hidden");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    done();
  }
});

/* Content editor ----------------------------------------------------------- */

function markDirty() {
  const dirty = JSON.stringify(state.store) !== state.pristine;
  $("dirty").classList.toggle("is-hidden", !dirty);
}

function field(label, value, onInput) {
  return el("label", { class: "field" }, [
    el("span", { text: label }),
    el("input", {
      type: "text",
      value: value ?? "",
      oninput: (event) => {
        onInput(event.target.value);
        markDirty();
      },
    }),
  ]);
}

function chipEditor(values, placeholder, onChange) {
  const add = el("input", { type: "text", placeholder });
  add.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const text = add.value.trim();
    if (!text) return;
    onChange([...values, text]);
  });

  return el("div", {}, [
    el(
      "div",
      { class: "chips" },
      values.length
        ? values.map((value, index) =>
            el("span", { class: "chip" }, [
              value,
              el("button", {
                type: "button",
                title: `Remove ${value}`,
                text: "×",
                onclick: () => onChange(values.filter((_, other) => other !== index)),
              }),
            ]),
          )
        : [el("span", { class: "empty-note", text: "None yet." })],
    ),
    el("div", { class: "add-row" }, [
      add,
      el("button", {
        class: "button button-small",
        type: "button",
        text: "Add",
        onclick: () => {
          const text = add.value.trim();
          if (text) onChange([...values, text]);
        },
      }),
    ]),
  ]);
}

function section(title, action, body) {
  return el("section", { class: "section" }, [
    el("header", {}, [el("h2", { text: title }), action]),
    el("div", { class: "body" }, body),
  ]);
}

function addButton(label, onClick) {
  return el("button", { class: "button button-small", type: "button", text: label, onclick: onClick });
}

function removeButton(label, onClick) {
  return el("button", {
    class: "button button-small button-danger",
    type: "button",
    text: label,
    onclick: onClick,
  });
}

function renderEditor() {
  const store = state.store;
  if (!store) return;

  const rerender = () => {
    markDirty();
    renderEditor();
  };

  /* Profile */
  const profile = section(
    "Profile",
    null,
    el("div", {}, [
      el("div", { class: "grid" }, [
        field("Name", store.profile.name, (value) => (store.profile.name = value)),
        field("Phone", store.profile.phone, (value) => (store.profile.phone = value)),
        field("Email", store.profile.email, (value) => (store.profile.email = value)),
      ]),
      el("p", { class: "subhead", text: "Links" }),
      chipEditor(store.profile.links, "linkedin.com/in/you", (links) => {
        store.profile.links = links;
        rerender();
      }),
    ]),
  );

  /* Skills */
  const skills = section(
    "Skills",
    addButton("Add group", () => {
      store.skills.push({ category: "New group", items: [] });
      rerender();
    }),
    store.skills.length
      ? store.skills.map((group, index) =>
          el("div", { class: "entry-card" }, [
            el("header", {}, [
              el("div", { class: "field" }, [
                el("input", {
                  type: "text",
                  value: group.category,
                  oninput: (event) => {
                    group.category = event.target.value;
                    markDirty();
                  },
                }),
              ]),
              removeButton("Remove group", () => {
                store.skills.splice(index, 1);
                rerender();
              }),
            ]),
            chipEditor(group.items, "Add a skill and press Enter", (items) => {
              group.items = items;
              rerender();
            }),
          ]),
        )
      : el("p", { class: "empty-note", text: "No skill groups yet." }),
  );

  /* Education */
  const education = section(
    "Education",
    addButton("Add school", () => {
      store.education.push({
        school: "",
        location: "",
        degree: "",
        graduation: "",
        coursework: [],
      });
      rerender();
    }),
    store.education.length
      ? store.education.map((entry, index) =>
          el("div", { class: "entry-card" }, [
            el("header", {}, [
              el("strong", { text: entry.school || "New school" }),
              removeButton("Remove", () => {
                store.education.splice(index, 1);
                rerender();
              }),
            ]),
            el("div", { class: "grid" }, [
              field("School", entry.school, (value) => (entry.school = value)),
              field("Location", entry.location, (value) => (entry.location = value)),
              field("Degree", entry.degree, (value) => (entry.degree = value)),
              field("Graduation", entry.graduation, (value) => (entry.graduation = value)),
            ]),
            el("p", { class: "subhead", text: "Relevant coursework" }),
            chipEditor(entry.coursework, "Add a course and press Enter", (coursework) => {
              entry.coursework = coursework;
              rerender();
            }),
          ]),
        )
      : el("p", { class: "empty-note", text: "No schools yet." }),
  );

  /* Projects and experience */
  const projects = section(
    "Experience and projects",
    addButton("Add entry", () => {
      store.projects.push({
        id: "",
        name: "New entry",
        role: "",
        organization: "",
        location: "",
        section: "project",
        dates: { start: "", end: "present" },
        technologies: [],
        domains: [],
        keywords: [],
        bullets: [],
        always_include: false,
      });
      rerender();
    }),
    store.projects.length
      ? store.projects.map((project, index) => renderProject(project, index, rerender))
      : el("p", { class: "empty-note", text: "No entries yet." }),
  );

  /* Leadership */
  const leadership = section(
    "Leadership and involvement",
    null,
    chipEditor(store.leadership, "Add a line and press Enter", (items) => {
      store.leadership = items;
      rerender();
    }),
  );

  $("editor").replaceChildren(profile, skills, education, projects, leadership);
}

function renderProject(project, index, rerender) {
  const store = state.store;

  const bullets = project.bullets.length
    ? project.bullets.map((text, position) =>
        el("div", { class: "bullet-row" }, [
          (() => {
            const area = el("textarea", { rows: "2" });
            area.value = text;
            area.addEventListener("input", (event) => {
              project.bullets[position] = event.target.value;
              markDirty();
            });
            return area;
          })(),
          el("div", { class: "bullet-tools" }, [
            el("button", {
              class: "icon-button",
              type: "button",
              title: "Move up",
              text: "↑",
              onclick: () => {
                if (position === 0) return;
                const [moved] = project.bullets.splice(position, 1);
                project.bullets.splice(position - 1, 0, moved);
                rerender();
              },
            }),
            el("button", {
              class: "icon-button danger",
              type: "button",
              title: "Remove bullet",
              text: "×",
              onclick: () => {
                project.bullets.splice(position, 1);
                rerender();
              },
            }),
          ]),
        ]),
      )
    : [el("p", { class: "empty-note", text: "No bullets yet. The tool can only pick from these." })];

  const sectionSelect = el("select", {
    onchange: (event) => {
      project.section = event.target.value;
      markDirty();
    },
  });
  for (const value of ["experience", "project"]) {
    const option = el("option", { value, text: value });
    if (project.section === value) option.selected = true;
    sectionSelect.appendChild(option);
  }

  return el("div", { class: "entry-card" }, [
    el("header", {}, [
      el("strong", { text: project.name || "New entry" }),
      removeButton("Delete entry", () => {
        if (!confirm(`Delete "${project.name}" and its bullets?`)) return;
        store.projects.splice(index, 1);
        rerender();
      }),
    ]),
    el("div", { class: "grid" }, [
      field("Name", project.name, (value) => (project.name = value)),
      field("Organization", project.organization, (value) => (project.organization = value)),
      field("Role", project.role, (value) => (project.role = value)),
      field("Location", project.location, (value) => (project.location = value)),
      el("label", { class: "field" }, [el("span", { text: "Section" }), sectionSelect]),
      field("Start", project.dates.start, (value) => (project.dates.start = value)),
      field("End", project.dates.end, (value) => (project.dates.end = value)),
    ]),

    el("p", { class: "subhead", text: "Technologies (these print on the resume)" }),
    chipEditor(project.technologies, "Add and press Enter", (items) => {
      project.technologies = items;
      rerender();
    }),

    el("p", { class: "subhead", text: "Domains (matching only, never printed)" }),
    chipEditor(project.domains, "backend, hardware, data…", (items) => {
      project.domains = items;
      rerender();
    }),

    el("p", { class: "subhead", text: "Keywords (matching only, never printed)" }),
    chipEditor(project.keywords, "CI/CD, real-time…", (items) => {
      project.keywords = items;
      rerender();
    }),

    el("p", { class: "subhead", text: "Bullets" }),
    ...bullets,
    el("div", { class: "add-row" }, [
      addButton("Add bullet", () => {
        project.bullets.push("");
        rerender();
      }),
      el("label", { class: "check" }, [
        (() => {
          const box = el("input", { type: "checkbox" });
          box.checked = Boolean(project.always_include);
          box.addEventListener("change", (event) => {
            project.always_include = event.target.checked;
            markDirty();
          });
          return box;
        })(),
        el("span", {}, [el("strong", { text: "Always include this entry" })]),
      ]),
    ]),
  ]);
}

async function loadStore() {
  try {
    state.store = await api("/api/store");
    state.pristine = JSON.stringify(state.store);
    renderEditor();
    markDirty();
  } catch (error) {
    toast(error.message, "error");
  }
}

$("save-button").addEventListener("click", async () => {
  const done = busy($("save-button"), "Saving");
  try {
    // Empty bullets are a normal editing state but not a valid store, so they
    // are dropped on the way out rather than rejected back at you.
    const payload = JSON.parse(JSON.stringify(state.store));
    for (const project of payload.projects) {
      project.bullets = project.bullets.filter((bullet) => bullet.trim());
    }
    state.store = await api("/api/store", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.pristine = JSON.stringify(state.store);
    renderEditor();
    markDirty();
    toast("Saved. The previous version is kept as projects.yaml.bak.", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    done();
  }
});

$("revert-button").addEventListener("click", () => {
  state.store = JSON.parse(state.pristine);
  renderEditor();
  markDirty();
});

window.addEventListener("beforeunload", (event) => {
  if (JSON.stringify(state.store) !== state.pristine) event.preventDefault();
});

async function boot() {
  const config = await api("/api/config");
  state.authRequired = Boolean(config.auth_required);
  state.supabaseUrl = config.supabase_url || "";
  state.supabaseAnonKey = config.supabase_anon_key || "";
  state.adminLoginEnabled = Boolean(config.admin_login_enabled);
  if (state.adminLoginEnabled) {
    $("login-admin").classList.remove("is-hidden");
  }

  // Need Supabase URL/anon key before exchanging a ?code= from the magic link.
  try {
    await consumeAuthRedirect();
  } catch (error) {
    toast(error.message, "error");
  }
  loadSession();

  if (!state.authRequired) {
    showApp();
    await loadHealth();
    await loadStore();
    return;
  }

  if (!state.session) {
    showLogin();
    await loadHealth();
    return;
  }

  try {
    await refreshSessionIfNeeded();
    if (!state.session) {
      showLogin();
      await loadHealth();
      return;
    }
    const me = await api("/api/me");
    state.userEmail = me.user.email;
    showApp();
    await loadHealth();
  } catch (error) {
    clearSession();
    showLogin();
    toast(error.message, "error");
    await loadHealth();
    return;
  }

  // Store load failures must not kick the user back to login.
  try {
    await loadStore();
  } catch (error) {
    toast(error.message, "error");
  }
}

boot().catch((error) => {
  showLogin();
  toast(error.message, "error");
});
