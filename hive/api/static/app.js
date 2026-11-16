/* HIVE — application du Conseil (PWA, sans dépendance, sans script tiers).
 * Sécurité : tout contenu venant de la ruche (sorties de modèles, web, médias) est rendu
 * via textContent — jamais innerHTML — pour empêcher toute injection dans la page. */
"use strict";

const $ = (s) => document.querySelector(s);
const state = { tab: "hive", lastSeq: 0, es: null, poll: null, files: [], current: null, recorder: null };

const LABELS = {
  fact: "fait", hypothesis: "hypothèse", unknown: "inconnu", proposed: "proposée", active: "active",
  done: "terminée", stopped: "arrêtée", cancelled: "annulée", pending: "en attente", failed: "échec",
  open: "ouverte", claimed: "en cours", rejected: "refusée", approved: "approuvée", awaiting_human: "attend le Conseil",
  waiting: "attend une décision", granted: "autorisée", blocked: "bloquée", expired: "expirée",
};
const KIND = {
  mission: "🎯 mission", task: "📋 tâche", observation: "👁 observation", belief: "🧭 croyance",
  contradiction: "⚡ contradiction", confirmation: "✔ confirmation", hypothesis: "💡 hypothèse",
  forecast: "🔮 prévision", risk: "⚠ risque", opportunity: "✨ opportunité", option: "🔀 option",
  proposal: "🗳 proposition", decision: "⚖ décision", simulation: "🧪 simulation", approval: "✋ validation",
  result: "🛠 résultat", report: "📄 livrable", measurement: "📏 mesure", lesson: "🎓 leçon",
  discovery: "💃 découverte", need: "🙋 besoin", message: "📣 consigne", artifact: "📎 média", dream: "🌙 rêve",
  clearance: "🛡 feu vert", spec: "📐 cahier des charges", script: "📝 scénario", translation: "🌍 traduction",
  frame: "🖼 illustration", voice: "🎙 voix", subtitles: "💬 sous-titres", video: "🎬 vidéo", qa: "🔬 contrôle qualité",
  delivery: "📦 livraison",
};
const STEP_LABELS = {
  observe: "Observer", understand: "Comprendre", confront: "Confronter", hypothesize: "Hypothèses",
  anticipate: "Anticiper", prioritize: "Prioriser", decide: "Décider", risk_review: "Risques",
  simulate: "Simuler", execute: "Exécuter", report: "Synthèse", measure: "Mesurer", learn: "Apprendre",
  revise: "Réviser", quorum: "Quorum", arbitrage: "Arbitrage", bilan: "Bilan", entraide: "Entraide",
  verification: "Vérification", cadrage: "Cadrage", scenario: "Scénario", traduction: "Traduction",
  illustration: "Illustration", voix: "Voix", sous_titres: "Sous-titres", montage: "Montage", controle: "Contrôle",
  correction: "Correction", revue: "Revue", livraison: "Livraison", lecons_studio: "Leçons",
};

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}
const tag = (text, cls = "") => el("span", { class: `tag ${cls}` }, text);
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") opts.headers["X-Hive"] = "1";
  if (form) opts.body = form;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (r.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("non authentifié");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `erreur ${r.status}`);
  return data;
}

/* ---------------------------------------------------------------- connexion */
function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  stopLive();
}

async function tryLogin(token) {
  try {
    await api("/api/login", { method: "POST", body: { token } });
    $("#login-error").textContent = "";
    start();
  } catch (e) {
    $("#login-error").textContent = e.message;
  }
}

$("#login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  tryLogin($("#token").value.trim());
});

/* ---------------------------------------------------------------- navigation */
function showTab(name) {
  state.tab = name;
  for (const t of document.querySelectorAll(".tab")) t.hidden = true;
  $(`#tab-${name}`).hidden = false;
  for (const b of document.querySelectorAll(".tabbar button"))
    b.classList.toggle("active", b.dataset.tab === name || (name === "mission" && b.dataset.tab === "missions"));
  if (name === "missions") loadMissions();
  if (name === "approvals") loadApprovals();
  if (name === "agents") loadAgents();
  window.scrollTo(0, 0);
}
for (const b of document.querySelectorAll(".tabbar button")) b.addEventListener("click", () => showTab(b.dataset.tab));
$("#btn-back").addEventListener("click", () => showTab("missions"));

/* ---------------------------------------------------------------- état global */
async function refreshStatus() {
  const s = await api("/api/status");
  const st = $("#pill-state");
  st.textContent = s.paused ? "EN PAUSE" : "active";
  st.className = `pill ${s.paused ? "off" : "on"}`;
  const kill = $("#btn-kill");
  kill.textContent = s.paused ? "▶" : "⏸";
  kill.classList.toggle("paused", s.paused);
  kill.setAttribute("aria-label", s.paused ? "Reprendre la ruche" : "Mettre la ruche en pause");
  $("#pill-budget").textContent = `${s.budget.spent.toFixed(2)} / ${s.budget.cap.toFixed(2)} €`;
  $("#st-active").textContent = s.missions.active + s.missions.awaiting_human;
  $("#st-tasks").textContent = s.open_tasks;
  $("#st-approvals").textContent = s.pending_approvals;
  const n = s.pending_approvals + s.proposed_lessons;
  const badge = $("#badge");
  badge.hidden = n === 0;
  badge.textContent = n;
  state.paused = s.paused;
}

$("#btn-kill").addEventListener("click", async () => {
  const pausing = !state.paused;
  if (pausing && !confirm("Mettre toute la ruche en pause ? Plus aucune abeille ne travaillera.")) return;
  await api(pausing ? "/api/colony/pause" : "/api/colony/resume", { method: "POST" });
  toast(pausing ? "Ruche en pause" : "Ruche relancée");
  refreshStatus();
});

/* ---------------------------------------------------------------- flux vivant */
function feedItem(ev) {
  const kind = ev.kind ? KIND[ev.kind] || ev.kind : ev.type;
  const meta = el("div", { class: "meta" }, el("span", {}, fmtTime(ev.ts)), el("b", {}, ev.actor), el("span", {}, kind),
    ev.status ? tag(LABELS[ev.status] || ev.status, ev.status) : null, ev.taint ? tag("données externes", "taint") : null);
  return el("li", {}, meta, ev.text ? el("div", {}, ev.text) : null);
}

function onEvent(ev) {
  if (ev.seq <= state.lastSeq) return;
  state.lastSeq = ev.seq;
  if (ev.type === "cell.reinforced" || ev.type === "reputation.updated") return;
  const feed = $("#feed");
  feed.prepend(feedItem(ev));
  while (feed.children.length > 120) feed.lastChild.remove();
  if (["cell.posted", "cell.updated", "colony.paused", "colony.resumed", "budget.cap_changed"].includes(ev.type)) {
    scheduleRefresh();
  }
}

let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshStatus().catch(() => {});
    if (state.tab === "mission" && state.current) openMission(state.current, true);
    if (state.tab === "missions") loadMissions();
    if (state.tab === "approvals") loadApprovals();
  }, 600);
}

function startLive() {
  stopLive();
  try {
    const es = new EventSource("/api/stream");
    state.es = es;
    es.onopen = () => $("#live-dot").classList.add("live");
    es.onmessage = (m) => onEvent(JSON.parse(m.data));
    es.onerror = () => {
      $("#live-dot").classList.remove("live");
      // repli : certains tunnels ne transmettent pas le SSE → interrogation régulière
      if (!state.poll) state.poll = setInterval(pollLedger, 4000);
    };
  } catch {
    state.poll = setInterval(pollLedger, 4000);
  }
}
function stopLive() {
  if (state.es) state.es.close();
  if (state.poll) clearInterval(state.poll);
  state.es = null;
  state.poll = null;
}
async function pollLedger() {
  const evs = await api(`/api/ledger?since=${state.lastSeq}&limit=200`).catch(() => []);
  evs.forEach(onEvent);
}

$("#directive-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#directive").value.trim();
  if (!text) return;
  await api("/api/directives", { method: "POST", body: { text } });
  $("#directive").value = "";
  toast("Directive transmise à la ruche");
});
$("#btn-dream").addEventListener("click", async () => {
  const d = await api("/api/dream", { method: "POST" });
  $("#hive-note").textContent = d.content;
});
$("#btn-verify").addEventListener("click", async () => {
  const v = await api("/api/ledger/verify");
  $("#hive-note").textContent = v.ok
    ? `Registre intact : ${v.events} événements chaînés et vérifiés.`
    : `ALERTE : registre altéré à l'événement ${v.first_bad_seq}.`;
});

/* ---------------------------------------------------------------- missions */
function renderAttachments() {
  const box = $("#attachments");
  box.replaceChildren(...state.files.map((f, i) =>
    el("span", { class: "chip" }, `${f.name} (${Math.ceil(f.size / 1024)} Ko)`,
      el("button", { type: "button", "aria-label": "Retirer", onclick: () => { state.files.splice(i, 1); renderAttachments(); } }, "✕"))));
}
$("#mission-files").addEventListener("change", (e) => {
  state.files.push(...e.target.files);
  e.target.value = "";
  renderAttachments();
});

$("#btn-record").addEventListener("click", async () => {
  const btn = $("#btn-record");
  if (state.recorder) {
    state.recorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    $("#record-note").textContent = "Micro indisponible (HTTPS requis hors localhost).";
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const rec = new MediaRecorder(stream);
    const chunks = [];
    rec.ondataavailable = (ev) => chunks.push(ev.data);
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = rec.mimeType || "audio/webm";
      const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
      state.files.push(new File(chunks, `vocal-${Date.now()}.${ext}`, { type }));
      state.recorder = null;
      btn.classList.remove("rec");
      $("#record-note").textContent = "Message vocal joint (transcrit par la ruche si un moteur est installé).";
      renderAttachments();
    };
    rec.start();
    state.recorder = rec;
    btn.classList.add("rec");
    $("#record-note").textContent = "Enregistrement… touchez 🎙️ pour arrêter.";
  } catch (err) {
    $("#record-note").textContent = `Micro refusé : ${err.message}`;
  }
});

$("#btn-dictate").addEventListener("click", () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    $("#record-note").textContent = "Dictée non prise en charge par ce navigateur : utilisez 🎙️.";
    return;
  }
  const r = new SR();
  r.lang = "fr-FR";
  r.interimResults = false;
  r.onresult = (ev) => {
    const t = $("#mission-text");
    t.value = `${t.value} ${ev.results[0][0].transcript}`.trim();
  };
  r.onerror = (ev) => ($("#record-note").textContent = `Dictée : ${ev.error}`);
  $("#record-note").textContent = "Dictée en cours (attention : certains navigateurs envoient l'audio à leurs serveurs).";
  r.start();
});

$("#mission-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData();
  form.append("text", $("#mission-text").value.trim());
  form.append("profile", $("#mission-profile").value);
  for (const f of state.files) form.append("files", f, f.name);
  const btn = e.submitter;
  if (btn) btn.disabled = true;
  try {
    const r = await api("/api/missions", { method: "POST", form });
    $("#mission-text").value = "";
    state.files = [];
    renderAttachments();
    toast(`Mission #${r.number} lancée (profil ${r.profile})`);
    openMission(r.id);
  } catch (err) {
    toast(err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
});

async function loadMissions() {
  const ms = await api("/api/missions");
  $("#missions").replaceChildren(...ms.map((m) =>
    el("li", { class: "card clickable", onclick: () => openMission(m.id) },
      el("h3", {}, `#${m.number ?? "?"} · ${m.text}`),
      el("div", { class: "tags" }, tag(m.status_fr || m.status, m.status), tag(m.profile),
        m.rev > 1 ? tag(`révision ${m.rev}`) : null, m.outcome ? tag(m.outcome, "done") : null,
        m.step ? tag(`en cours : ${m.step}`) : null,
        typeof m.score === "number" ? tag(`score ${Math.round(m.score * 100)} %`) : null,
        tag(fmtTime(m.created_at))))));
  if (!ms.length) $("#missions").append(el("li", { class: "muted small" }, "Aucune mission pour l'instant."));
}

function speak(text) {
  if (!("speechSynthesis" in window)) return toast("Synthèse vocale indisponible");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "fr-FR";
  speechSynthesis.speak(u);
}

const artUrl = (id) => `/api/artifacts/${encodeURIComponent(id)}`;

function mediaFor(d) {
  const id = d.artifact || (d.input ? d.id : null);
  if (!id) return null;
  if (d.media === "video" || d.mime === "video/mp4") return el("video", { src: `${artUrl(id)}#t=1.5`, controls: true, playsinline: true, preload: "metadata", class: "media" });
  if (d.media === "audio" || (d.mime || "").startsWith("audio/")) return el("audio", { src: artUrl(id), controls: true, preload: "none", class: "full" });
  if (d.media === "image" && ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(d.mime)) return el("img", { src: artUrl(id), alt: d.name || d.scene || "image", class: "media", loading: "lazy" });
  return el("a", { href: artUrl(id), download: "" }, "⬇ télécharger");
}

function cellCard(c) {
  const isReport = c.kind === "report";
  const d = c.data || {};
  const extras = [];
  if (["artifact", "video", "voice", "subtitles", "frame"].includes(c.kind)) {
    const m = mediaFor(d);
    if (m) extras.push(m);
  }
  if (c.kind === "qa" && Array.isArray(d.checks)) {
    extras.push(el("ul", { class: "small checks" }, d.checks.map((k) =>
      el("li", { class: k.ok ? "ok" : (k.critical ? "ko" : "warn") }, `${k.ok ? "✓" : k.critical ? "✗" : "!"} ${k.check}${k.detail ? ` — ${k.detail}` : ""}`))));
  }
  const tags = [];
  if (d.option_key && c.kind !== "decision") tags.push(tag(`option : ${d.option_key}`));
  if (typeof d.probability === "number") tags.push(tag(`p = ${Math.round(d.probability * 100)} %`));
  if (d.veto) tags.push(tag("VETO", "taint"));
  if (d.tally) tags.push(tag(`vote : ${Object.entries(d.tally).map(([k, v]) => `${k} ${v}`).join(" · ")}`));
  if (typeof d.score === "number") tags.push(tag(`score ${Math.round(d.score * 100)} %`));
  if (d.downgraded) tags.push(tag("rétrogradé : pas de preuve", "hypothesis"));
  if (d.exec_status) tags.push(tag(`exécution : ${d.exec_status}`));
  if (d.cached_from) tags.push(tag("réutilisé (cache)"));
  return el("div", { class: `card cell ${isReport ? "report" : ""}` },
    el("div", { class: "tags" }, tag(KIND[c.kind] || c.kind), el("b", { class: "small" }, c.author),
      c.status ? tag(LABELS[c.status] || c.status, c.status) : null,
      c.confidence != null ? tag(`conf. ${Math.round(c.confidence * 100)} %`) : null,
      c.taint ? tag("données externes", "taint") : null, c.refs?.length ? tag(`${c.refs.length} preuve(s)`) : null),
    c.kind === "frame" ? null : el("div", { class: "mt" }, c.content),
    tags.length ? el("div", { class: "tags" }, tags) : null,
    extras.length ? el("div", { class: "mt" }, extras) : null,
    isReport ? el("div", { class: "feedback" }, el("button", { class: "ghost", onclick: () => speak(c.content) }, "🔊 Écouter")) : null);
}

async function openMission(id, silent = false) {
  state.current = id;
  if (!silent) showTab("mission");
  const { mission: m, cells, brief: b } = await api(`/api/missions/${encodeURIComponent(id)}`);
  const rev = b.rev;
  const workers = el("div", { class: "steps" }, Object.entries(b.workers).map(([w, st]) =>
    el("span", { class: `step ${st === "✓" ? "done" : st === "…" ? "current" : ""}` }, STEP_LABELS[w] || w)));
  const note = el("textarea", { rows: 2, maxlength: 2000, placeholder: "Consigne pour une nouvelle révision (ex. « ajoute le violet »)…" });
  const head = el("div", { class: "card" }, el("h3", {}, `#${b.number} · ${m.content}`),
    el("div", { class: "tags" }, tag(b.status_fr, m.status), tag(b.profile), tag(`révision ${rev}`),
      b.outcome ? tag(b.outcome, "done") : null,
      typeof b.score === "number" ? tag(`score ${Math.round(b.score * 100)} %`) : null,
      b.end_reason ? tag(b.end_reason) : null, tag(`budget ${b.budget.spent} / ${b.budget.cap} €`)), workers,
    b.history.length > 1 ? el("details", {}, el("summary", { class: "small muted" }, `Historique (${b.history.length - 1} révision(s))`),
      ...b.history.slice(1).map((h) => el("div", { class: "small" }, `Rév. ${h.rev} · ${h.auto ? "correction automatique" : h.by} : ${h.note}`))) : null,
    ["done", "stopped"].includes(m.status) ? el("div", { class: "mt" }, note,
      el("button", { class: "ghost", onclick: async () => {
        if (!note.value.trim()) return toast("Écrivez une consigne.");
        await api(`/api/missions/${encodeURIComponent(id)}/revise`, { method: "POST", body: { note: note.value.trim() } });
        toast("Révision lancée"); openMission(id, true);
      } }, "🔁 Demander une révision")) : null,
    ["active", "awaiting_human"].includes(m.status) ? el("button", { class: "ghost danger", onclick: async () => {
      if (!confirm("Annuler cette mission ?")) return;
      await api(`/api/missions/${encodeURIComponent(id)}/cancel`, { method: "POST" });
      openMission(id, true);
    } }, "Annuler la mission") : null);
  const current = cells.filter((c) => c.kind !== "task" && c.kind !== "mission" && (c.rev === rev || c.kind === "artifact"));
  const blocks = [head];
  for (const a of b.pending_approvals) blocks.push(el("div", { class: "card warnbox" }, "✋ ", a.title, " → onglet Valider"));
  const deliverables = current.filter((c) => ["report", "video"].includes(c.kind));
  if (deliverables.length) {
    blocks.push(el("div", { class: "phase-title" }, "Livrables"), ...deliverables.map(cellCard));
    blocks.push(el("div", { class: "feedback" },
      el("button", { class: "ok", onclick: () => sendFeedback(id, 1) }, "👍 Utile"),
      el("button", { class: "ghost", onclick: () => sendFeedback(id, 0.5) }, "😐 Mitigé"),
      el("button", { class: "no", onclick: () => sendFeedback(id, 0) }, "👎 Inutile")));
  }
  const frames = current.filter((c) => c.kind === "frame");
  if (frames.length) blocks.push(el("div", { class: "phase-title" }, `Illustrations (${frames.length})`),
    el("div", { class: "grid" }, frames.map((f) => mediaFor(f.data))));
  let lastPhase = null;
  for (const c of current) {
    if (["report", "video", "frame"].includes(c.kind)) continue;
    const ph = c.phase || (c.kind === "artifact" ? "observe" : "autre");
    if (ph !== lastPhase) {
      blocks.push(el("div", { class: "phase-title" }, STEP_LABELS[ph] || ph));
      lastPhase = ph;
    }
    blocks.push(cellCard(c));
  }
  const older = cells.filter((c) => c.rev < rev && !["task", "mission", "artifact"].includes(c.kind));
  if (older.length) blocks.push(el("details", {}, el("summary", { class: "muted small" }, `Révisions précédentes (${older.length} traces)`),
    ...older.map(cellCard)));
  const tasks = cells.filter((c) => c.kind === "task" && c.rev === rev);
  blocks.push(el("details", {}, el("summary", { class: "muted small" }, `Travaux et enchères (${tasks.length})`),
    ...tasks.map((t) => el("div", { class: "card small" }, `${t.content} — ${LABELS[t.status] || t.status}`,
      t.claimed_by ? ` · ${t.claimed_by}` : "", t.data?.cached ? " · cache" : "",
      t.data?.bids ? el("pre", { class: "json" }, JSON.stringify(t.data.bids)) : null,
      t.data?.error ? el("div", { class: "danger" }, t.data.error) : null))));
  $("#mission-detail").replaceChildren(...blocks);
}

async function sendFeedback(id, score) {
  await api(`/api/missions/${encodeURIComponent(id)}/feedback`, { method: "POST", body: { score } });
  toast("Merci : votre retour ajuste la réputation des abeilles.");
}

/* ---------------------------------------------------------------- validations */
async function loadApprovals() {
  const d = await api("/api/approvals");
  $("#approvals").replaceChildren(...(d.approvals.length ? d.approvals.map((a) => {
    const as = a.data.assessment || {};
    const isDelivery = a.data.action?.type === "delivery";
    const comment = el(isDelivery ? "textarea" : "input", { placeholder: isDelivery ? "Motif d'un refus : il deviendra la consigne de la révision suivante" : "Commentaire (optionnel)", maxlength: 1000, class: "full mt", rows: 2 });
    const act = async (approved) => {
      if (!approved && isDelivery && !comment.value.trim()) return toast("Indiquez ce qui doit changer.");
      if (approved && as.risk >= 2 && !confirm("Risque élevé : confirmer l'autorisation ?")) return;
      await api(`/api/approvals/${encodeURIComponent(a.id)}`, { method: "POST", body: { approved, comment: comment.value } });
      toast(approved ? "Autorisé" : (isDelivery ? "Refusé : révision lancée" : "Refusé"));
      loadApprovals();
      refreshStatus();
    };
    const previews = (a.data.preview || []).map((id) => el("video", { src: `${artUrl(id)}#t=1.5`, controls: true, playsinline: true, preload: "metadata", class: "media" }));
    return el("div", { class: "card" }, el("h3", {}, a.content),
      el("div", { class: `small risk-${as.risk}` }, `Risque ${as.risk_label || "?"}`),
      el("ul", { class: "small" }, (as.reasons || []).map((r) => el("li", {}, r))),
      ...previews,
      el("div", { class: "small muted mt" }, a.data.simulation || ""),
      isDelivery ? null : el("pre", { class: "json" }, JSON.stringify(a.data.action, null, 1)), comment,
      el("div", { class: "decide" }, el("button", { class: "ok", onclick: () => act(true) }, isDelivery ? "✅ Livrer" : "Autoriser"),
        el("button", { class: "no", onclick: () => act(false) }, isDelivery ? "⛔ Refuser et réviser" : "Refuser")));
  }) : [el("p", { class: "muted small" }, "Rien à valider. La ruche travaille.")]));

  $("#lessons").replaceChildren(...(d.lessons.length ? d.lessons.map((l) => el("div", { class: "card" },
    el("div", {}, l.content), el("div", { class: "tags" }, tag(l.author), l.taint ? tag("issue de données externes", "taint") : null),
    el("div", { class: "decide" },
      el("button", { class: "ok", onclick: async () => { await api(`/api/lessons/${l.id}`, { method: "POST", body: { approved: true } }); loadApprovals(); } }, "Activer"),
      el("button", { class: "no", onclick: async () => { await api(`/api/lessons/${l.id}`, { method: "POST", body: { approved: false } }); loadApprovals(); } }, "Rejeter"))))
    : [el("p", { class: "muted small" }, "Aucune leçon en attente.")]));

  $("#needs").replaceChildren(...(d.needs.length ? d.needs.map((n) => el("div", { class: "card" },
    el("div", {}, n.content), el("div", { class: "decide" },
      el("button", { class: "ghost", onclick: async () => { await api(`/api/needs/${n.id}/close`, { method: "POST" }); loadApprovals(); } }, "Marquer comme traité"))))
    : [el("p", { class: "muted small" }, "Aucune question.")]));

  $("#forecasts").replaceChildren(...(d.forecasts.length ? d.forecasts.map((f) => el("div", { class: "card" },
    el("div", {}, f.content), el("div", { class: "small muted" }, `Critère : ${f.data.resolution || "—"} · par ${f.author}`),
    el("div", { class: "decide" },
      el("button", { class: "ok", onclick: async () => { await api(`/api/forecasts/${f.id}`, { method: "POST", body: { outcome: true } }); loadApprovals(); } }, "S'est réalisée"),
      el("button", { class: "no", onclick: async () => { await api(`/api/forecasts/${f.id}`, { method: "POST", body: { outcome: false } }); loadApprovals(); } }, "Ne s'est pas réalisée"))))
    : [el("p", { class: "muted small" }, "Aucune prévision ouverte.")]));
}

/* ---------------------------------------------------------------- abeilles */
async function loadAgents() {
  const d = await api("/api/agents");
  $("#agents").replaceChildren(...d.agents.map((a) => {
    const rep = a.reputation || {};
    return el("div", { class: "card" }, el("h3", {}, `🐝 ${a.title}`), el("div", { class: "small muted" }, a.role),
      el("div", { class: "tags" }, Object.entries(a.skills).map(([s, v]) =>
        tag(`${s} ${Math.round(v * 100)}${rep.skills?.[s] != null ? ` → ${Math.round(rep.skills[s] * 100)}` : ""}`))),
      el("div", { class: "tags" }, tag(`outils : ${a.tools.join(", ")}`), tag(`modèles : ${a.models.join(" › ")}`),
        tag(`tâches ${rep.tasks_done || 0} ✓ / ${rep.tasks_failed || 0} ✗`),
        rep.brier != null ? tag(`Brier ${rep.brier}`) : null, a.ephemeral ? tag("éphémère") : null));
  }));
  $("#providers").replaceChildren(...d.providers.map((p) => el("div", { class: "card small" },
    el("b", {}, p.name), ` — ${p.kind} · ${p.model || "—"} `, tag(p.free ? "gratuit" : "PAYANT", p.free ? "fact" : "taint"),
    p.vision ? tag("vision") : null)));
}

$("#budget-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/budget/request", { method: "POST", body: { cap: Number($("#budget-cap").value), reason: $("#budget-reason").value } });
  toast("Demande créée : validez-la dans l'onglet Valider.");
  refreshStatus();
});
$("#btn-logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin();
});

/* ---------------------------------------------------------------- démarrage */
async function start() {
  try {
    await refreshStatus();
  } catch {
    return showLogin();
  }
  $("#login").hidden = true;
  $("#app").hidden = false;
  const recent = await api("/api/ledger?limit=60").catch(() => []);
  recent.forEach(onEvent);
  startLive();
  showTab(state.tab === "mission" ? "missions" : state.tab);
  setInterval(() => refreshStatus().catch(() => {}), 15000);
}

(async () => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  // Lien de connexion « #token=… » : le fragment n'est jamais envoyé au serveur ni journalisé.
  const m = location.hash.match(/token=([^&]+)/);
  if (m) {
    history.replaceState(null, "", location.pathname);
    await tryLogin(decodeURIComponent(m[1]));
    return;
  }
  start();
})();
