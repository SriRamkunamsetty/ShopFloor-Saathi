// ShopFloor Saathi PWA. No build step. All dynamic text is inserted with textContent (no innerHTML),
// so server or model output can never inject markup.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { me: null, machines: [], selected: null, sessionId: null, photo: null };

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
const num = (n, d = 0) => Number(n).toLocaleString("en-IN", { maximumFractionDigits: d });

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.me?.auth_mode !== "firebase") {
    headers["X-Saathi-Role"] = localStorage.getItem("saathi.role") || "operator";
  } else if (window.saathiIdToken) {
    headers["Authorization"] = "Bearer " + (await window.saathiIdToken());
  }
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${res.status})`;
    throw new Error(detail);
  }
  return body;
}

const isSupervisor = () => state.me?.role === "supervisor";

// ---------- impact + fleet ----------
async function refreshImpact() {
  const s = await api("/api/impact/summary");
  $("kpi-inr").textContent = inr(s.total_inr);
  $("kpi-min").textContent = num(s.downtime_minutes_avoided);
  $("kpi-co2").textContent = num(s.co2_kg_avoided, 1);
  $("kpi-pending").textContent = num(s.pending_approval);
}

function renderFleet() {
  const fleet = $("fleet");
  fleet.replaceChildren(
    ...state.machines.map((m) =>
      h("button", {
        type: "button", role: "listitem", class: `machine ${m.status}`,
        "aria-pressed": String(state.selected === m.machine_id),
        onclick: () => selectMachine(m.machine_id),
      },
        h("span", { class: `status ${m.status}` }, m.status),
        h("span", { class: "name" }, `${m.machine_id} · ${m.name}`),
        h("span", { class: "meta" },
          `gap ${m.latest.temp_gap_k} K · ${num(m.latest.rpm)} rpm · wear ${num(m.latest.tool_wear_min)} min`),
        m.findings[0] ? h("span", { class: "meta" }, m.findings[0].title) : null,
      )),
  );
  const sel = $("machine-select");
  sel.replaceChildren(...state.machines.map((m) => h("option", { value: m.machine_id }, `${m.machine_id} · ${m.name}`)));
  if (state.selected) sel.value = state.selected;
}

function selectMachine(id) {
  state.selected = id;
  $("machine-select").value = id;
  renderFleet();
  $("diag-h").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function refreshFleet() {
  state.machines = await api("/api/machines");
  if (!state.selected && state.machines.length) state.selected = state.machines[0].machine_id;
  renderFleet();
}

// ---------- diagnosis ----------
function citeEl(c) {
  return h("div", { class: "cite" },
    h("strong", {}, `${c.source} › ${c.heading}`), h("br"), c.excerpt);
}

function draftEl(d) {
  return h("div", { class: "card" },
    h("h3", {}, d.title),
    h("div", {}, h("span", { class: `pill ${d.priority}` }, d.priority), `Planned downtime ~${d.est_downtime_min} min`),
    h("h3", {}, "Steps"),
    h("ol", {}, d.steps.map((s) => h("li", {}, s))),
    h("h3", {}, "Parts"),
    h("ul", {}, d.parts.map((p) => h("li", {},
      h("span", { class: `pill ${p.in_stock ? "ok" : "bad"}` }, p.in_stock ? "in stock" : "missing"),
      `${p.part} (${p.description})`,
      p.in_stock ? (p.bin ? ` · bin ${p.bin}` : "") : ` · lead time ${p.lead_time_days ?? "?"} days`))),
    d.safety_notes.map((n) => h("p", { class: "safety" }, n)),
    h("h3", {}, "Projected impact"),
    h("p", {}, `${inr(d.impact.total_inr)} (downtime ${inr(d.impact.downtime_cost_avoided_inr)}, energy ${inr(d.impact.energy_cost_saved_inr)}) · ` +
      `${num(d.impact.downtime_minutes_avoided)} min · ${num(d.impact.energy_kwh_saved, 1)} kWh · ${num(d.impact.co2_kg_avoided, 1)} kg CO₂`),
    h("details", {}, h("summary", {}, "Assumptions"),
      h("ul", {}, Object.entries(d.impact.assumptions).map(([k, v]) => h("li", {}, `${k}: ${v}`)))),
    h("p", { class: "muted" }, d.impact.note),
  );
}

function renderReport(resp) {
  const r = resp.report;
  const box = $("report");
  const parts = [];
  if (r.abstained) {
    parts.push(h("div", { class: "card" }, h("h3", {}, "I don't know yet"), h("p", {}, r.abstain_reason)));
  }
  if (r.advisory_note) parts.push(h("div", { class: "card" }, h("p", {}, r.advisory_note)));
  for (const hyp of r.hypotheses) {
    parts.push(h("div", { class: "card" },
      h("h3", {}, hyp.label),
      h("meter", { min: 0, max: 1, value: hyp.confidence, "aria-label": "confidence" }),
      h("p", { class: "muted" }, `Confidence ${Math.round(hyp.confidence * 100)}% (heuristic) · ` +
        (hyp.supported_by_telemetry ? "backed by telemetry" : "manual only, verify on the machine")),
      h("ul", {}, hyp.reasoning.map((x) => h("li", {}, x))),
      hyp.citations.map(citeEl)));
  }
  if (r.recommended) {
    parts.push(draftEl(r.recommended));
    if (resp.work_order) {
      const wo = resp.work_order;
      const pending = wo.status === "pending_approval";
      parts.push(h("p", {}, h("span", { class: "pill" }, wo.status.replace("_", " ")),
        `Work order ${wo.id}` + (resp.work_order_created ? " drafted." : " already open.") +
        (pending ? " A supervisor must approve it before work starts." : "")));
    }
  }
  if (r.similar_incidents.length) {
    parts.push(h("details", {}, h("summary", {}, `Similar past incidents (${r.similar_incidents.length})`), r.similar_incidents.map(citeEl)));
  }
  parts.push(h("details", {}, h("summary", {}, "How I got here"), h("ol", { class: "trace" }, r.trace.map((t) => h("li", {}, t)))));
  box.replaceChildren(...parts);
}

$("diag-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.submitter; btn.disabled = true;
  try {
    const resp = await api("/api/diagnose", {
      method: "POST",
      body: JSON.stringify({ machine_id: $("machine-select").value, symptom: $("symptom").value }),
    });
    renderReport(resp);
    await Promise.all([refreshOrders(), refreshImpact()]);
  } catch (err) {
    $("report").replaceChildren(h("p", { class: "error", role: "alert" }, err.message));
  } finally { btn.disabled = false; }
});

// ---------- work orders ----------
async function decide(id, decision) {
  try {
    await api(`/api/workorders/${id}/decision`, { method: "POST", body: JSON.stringify({ decision }) });
  } catch (err) { alert(err.message); }
  await Promise.all([refreshOrders(), refreshImpact()]);
}

async function complete(id) {
  const raw = prompt("Actual downtime in minutes?");
  const minutes = Number(raw);
  if (raw === null || !Number.isFinite(minutes) || minutes < 0) return;
  try {
    await api(`/api/workorders/${id}/complete`, { method: "POST", body: JSON.stringify({ actual_downtime_min: minutes }) });
  } catch (err) { alert(err.message); }
  await refreshOrders();
}

async function refreshOrders() {
  const orders = await api("/api/workorders");
  const box = $("orders");
  if (!orders.length) { box.replaceChildren(h("p", { class: "muted" }, "No work orders yet.")); return; }
  box.replaceChildren(...orders.map((o) => h("div", { class: "card" },
    h("div", {}, h("span", { class: `pill ${o.draft.priority}` }, o.draft.priority),
      h("span", { class: "pill" }, o.status.replace("_", " ")), h("strong", {}, o.id)),
    h("p", {}, o.draft.title),
    h("p", { class: "muted" }, `Projected ${inr(o.draft.impact.total_inr)} · requested by ${o.created_by}` +
      (o.decided_by ? ` · decided by ${o.decided_by}` : "")),
    isSupervisor() && o.status === "pending_approval"
      ? h("div", { class: "row" },
          h("button", { class: "primary", onclick: () => decide(o.id, "approve") }, "Approve"),
          h("button", { class: "danger", onclick: () => decide(o.id, "reject") }, "Reject"))
      : null,
    isSupervisor() && o.status === "approved"
      ? h("button", { onclick: () => complete(o.id) }, "Mark completed") : null,
    !isSupervisor() && o.status === "pending_approval" ? h("p", { class: "muted" }, "Waiting for a supervisor.") : null,
  )));
}

// ---------- chat ----------
function addMsg(kind, text, notice) {
  const el = h("div", { class: `msg ${kind}` }, text, notice ? h("span", { class: "notice" }, notice) : null);
  $("chat-log").append(el);
  el.scrollIntoView({ block: "end" });
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the photo"));
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(file);
  });
}

$("photo").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  const note = $("photo-note");
  if (!file) { state.photo = null; note.hidden = true; return; }
  if (file.size > 4 * 1024 * 1024) {
    state.photo = null; note.hidden = false; note.textContent = "Photo is larger than 4 MB, please take a smaller one."; return;
  }
  state.photo = { mime: file.type, data: await readFileAsBase64(file) };
  note.hidden = false; note.textContent = `Photo attached (${file.name}). It is sent with your next message.`;
});

$("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  addMsg("user", text + (state.photo ? " 📷" : ""));
  input.value = "";
  const body = { message: text, session_id: state.sessionId };
  if (state.photo) { body.image_base64 = state.photo.data; body.image_mime = state.photo.mime; }
  state.photo = null; $("photo").value = ""; $("photo-note").hidden = true;
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify(body) });
    state.sessionId = r.session_id;
    addMsg("bot", r.reply, r.notice);
    if (r.work_order_id) await Promise.all([refreshOrders(), refreshImpact()]);
  } catch (err) {
    addMsg("bot", "Sorry: " + err.message);
  }
});

// ---------- voice (Web Speech API; availability depends on the browser) ----------
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const mic = $("mic");
if (!Recognition) {
  mic.disabled = true; mic.title = "Voice input is not supported in this browser";
} else {
  const rec = new Recognition();
  rec.interimResults = false; rec.maxAlternatives = 1;
  rec.onresult = (ev) => { $("chat-input").value = ev.results[0][0].transcript; };
  rec.onend = () => mic.classList.remove("listening");
  rec.onerror = () => mic.classList.remove("listening");
  mic.addEventListener("click", () => {
    if (mic.classList.contains("listening")) { rec.stop(); return; }
    rec.lang = $("voice-lang").value;
    mic.classList.add("listening");
    try { rec.start(); } catch { mic.classList.remove("listening"); }
  });
}

// ---------- boot ----------
async function boot() {
  const roleSel = $("role-select");
  roleSel.value = localStorage.getItem("saathi.role") || "operator";
  roleSel.addEventListener("change", async () => {
    localStorage.setItem("saathi.role", roleSel.value);
    state.me = await api("/api/me");
    await refreshOrders();
  });
  try {
    state.me = await api("/api/me");
    $("role-wrap").hidden = state.me.auth_mode !== "dev";
    const badge = $("mode-badge");
    badge.textContent = state.me.assistant_mode === "gemini" ? "Gemini assistant" : "Offline rules mode";
    badge.className = `badge ${state.me.assistant_mode}`;
    await Promise.all([refreshFleet(), refreshOrders(), refreshImpact()]);
    addMsg("bot", "Namaste! Tell me the machine and the problem, in any language. For example: “M-07 garam ho raha hai”.");
  } catch (err) {
    $("fleet").replaceChildren(h("p", { class: "error", role: "alert" }, err.message));
  }
  setInterval(() => { refreshFleet().catch(() => {}); }, 30000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
boot();
