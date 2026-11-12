/* Goal Engine admin panel: overview, enquiries, people, expert requests, DBaba knowledge and learning.
   Owner only (the admin cookie); every call answers 404 to anyone else. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
  const btn = (cls, text, fn) => { const b = el("button", cls, text); b.type = "button"; if (fn) b.addEventListener("click", fn); return b; };
  const SVG = "http://www.w3.org/2000/svg";
  const sv = (tag, attrs) => { const e = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v); return e; };
  const when = (iso) => iso ? new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "never";
  const sum = (a) => a.reduce((x, y) => x + y, 0);

  // ---------------------------------------------------------------- theme
  const root = document.documentElement;
  try { const t = localStorage.getItem("ge_admin_theme"); if (t) root.dataset.theme = t; } catch (_) { /* ignore */ }
  $("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("ge_admin_theme", root.dataset.theme); } catch (_) { /* ignore */ }
  });

  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 1800);
  }

  async function api(path, body, method) {
    const r = await fetch(path, { method: method || (body === undefined ? "GET" : "POST"), credentials: "same-origin", cache: "no-store",
                                  headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (r.status === 404) throw new Error("Not signed in. Open your private admin link again.");
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || data.error || "Something went wrong.");
    return data;
  }

  // ---------------------------------------------------------------- charts (inline SVG)
  function bars(series, labels) {
    const box = el("div", "chart");
    const n = series[0].values.length, W = 600, H = 120, gap = 2;
    const max = Math.max(1, ...series.flatMap((s) => s.values));
    const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img" });
    const bw = W / n;
    series.forEach((s, si) => {
      const w = (bw - gap) / series.length;
      s.values.forEach((v, i) => {
        const h = v ? Math.max(2, (v / max) * (H - 4)) : 0;
        const r = sv("rect", { x: i * bw + si * w, y: H - h, width: Math.max(1, w), height: h, rx: 1.5, fill: s.color });
        const t = sv("title"); t.textContent = `${labels[i]}: ${v} ${s.name.toLowerCase()}`; r.append(t);
        svg.append(r);
      });
    });
    box.append(svg);
    const ax = el("div", "axis"); ax.append(el("span", null, labels[0].slice(5)), el("span", null, "today")); box.append(ax);
    if (series.length > 1) {
      const lg = el("div", "legend");
      for (const s of series) { const i = el("i"); i.style.background = s.color; const sp = el("span"); sp.append(i, s.name); lg.append(sp); }
      box.append(lg);
    }
    return box;
  }
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  function tile(k, v, s, hot) {
    const t = el("div", `tile${hot ? " hot" : ""}`); t.append(el("div", "k", k), el("div", "v", String(v))); if (s) t.append(el("div", "s", s)); return t;
  }
  function panel(title, note, ...kids) {
    const p = el("div", "panel"); p.append(el("h2", null, title)); if (note) p.append(el("p", "note", note)); p.append(...kids); return p;
  }

  // ---------------------------------------------------------------- views
  const views = {
    overview: { title: "Overview", load: overview },
    enquiries: { title: "Enquiries", load: enquiries },
    people: { title: "People with accounts", load: people },
    experts: { title: "Expert requests", load: experts },
    dbaba: { title: "DBaba knowledge", load: dbaba },
    learning: { title: "Learning", load: learning },
  };
  let current = "overview";

  async function show(name) {
    if (!views[name]) name = "overview";
    current = name;
    for (const a of document.querySelectorAll("[data-view]")) {
      if (a.dataset.view === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    }
    for (const k of Object.keys(views)) $(`v-${k}`).hidden = k !== name;
    $("title").textContent = views[name].title; $("sub").textContent = "";
    const box = $(`v-${name}`);
    if (!box.childElementCount) box.append(el("p", "empty", "Loading…"));
    try { await views[name].load(box); }
    catch (ex) { box.replaceChildren(el("p", "empty", ex.message)); }
  }
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  $("reload").addEventListener("click", () => show(current));

  function badge(id, n) { const b = $(id); b.textContent = n; b.hidden = !n; }

  async function overview(box) {
    const d = await api("/api/admin/overview");
    badge("bEnq", d.enquiries.new); badge("bExp", d.experts_new); badge("bLearn", d.learning.to_learn + d.learning.said_wrong);
    $("sub").textContent = "last 30 days";
    const w = d.workspace;
    const tiles = el("div", "tiles");
    tiles.append(
      tile("People this week", d.people_week, `${d.people_month} in 30 days (anyone who typed)`),
      tile("Accounts", d.accounts.total, `${d.accounts.verified} verified · ${d.accounts.active_week} active this week`),
      tile("Workspace questions", sum(w.workspace_ask), `${sum(w.workspace_run)} queries run · ${sum(w.workspace_connect)} connects`),
      tile("Enquiries", d.enquiries.total, `${d.enquiries.new} new`, d.enquiries.new > 0),
      tile("Expert requests", d.experts_new, "waiting", d.experts_new > 0),
      tile("Databases connected", d.connections.ok, `${d.connections.failed} failed tries`),
    );
    const g1 = el("div", "grid2");
    g1.append(
      panel("People per day", "Different people who typed anything in the chat.",
            bars([{ name: "People", values: d.people, color: css("--bar") }], d.days)),
      panel("DBaba workspace per day", "Counts only: what was asked is never kept.",
            bars([{ name: "Questions", values: w.workspace_ask, color: css("--bar") },
                  { name: "Queries run", values: w.workspace_run, color: css("--bar-2") }], d.days)),
    );
    const t = el("table"); const head = el("tr");
    for (const h of ["Area", "This week", "30 days", "All time"]) head.append(el("th", null, h));
    t.append(head);
    for (const a of d.areas) {
      const tr = el("tr"); tr.append(el("td", null, a.label), el("td", "n", a.week), el("td", "n", a.month), el("td", "n", a.total)); t.append(tr);
    }
    const g2 = el("div", "grid2");
    g2.append(
      panel("Which parts people use", "Agents started for a goal, by area.", t),
      panel("New accounts and enquiries", null,
            bars([{ name: "Accounts", values: d.accounts.signups, color: css("--bar") },
                  { name: "Enquiries", values: d.enquiries.per_day, color: css("--bar-2") }], d.days)),
    );
    box.replaceChildren(tiles, g1, g2);
  }

  // ---------------------------------------------------------------- enquiries
  let enqStatus = "new";
  const ENQ = { "": "All", new: "New", talking: "Talking", customer: "Customer", closed: "Closed", spam: "Spam" };
  async function enquiries(box) {
    const d = await api(`/api/admin/enquiries?status=${encodeURIComponent(enqStatus)}`);
    badge("bEnq", d.counts.new || 0);
    $("sub").textContent = `${sum(Object.values(d.counts))} in all`;
    const chips = el("div", "chips");
    for (const [k, v] of Object.entries(ENQ)) {
      const n = k ? (d.counts[k] || 0) : sum(Object.values(d.counts));
      const b = btn(null, `${v} ${n}`, () => { enqStatus = k; enquiries(box); }); b.setAttribute("aria-pressed", String(k === enqStatus)); chips.append(b);
    }
    const share = el("p", "muted");
    share.append("The public form is at ", Object.assign(el("a", null, "/enquiry"), { href: "/enquiry", target: "_blank" }),
                 ". Add ?about=dbaba (or sheetu, website, workspace, automation) to tick that box for people.");
    const list = d.enquiries.map((e) => {
      const c = el("article", `card ${e.status}`);
      const top = el("div", "top"); top.append(el("b", null, e.name));
      if (e.company) top.append(el("span", "muted", e.company));
      top.append(el("span", "when", when(e.created_at)));
      const tags = el("div");
      for (const i of e.interest) tags.append(el("span", "tag warn", i));
      for (const x of [e.role, e.team && `team ${e.team}`, e.user_id && "has an account", e.source && `from ${e.source}`]) if (x) tags.append(el("span", "tag", x));
      const acts = el("div", "acts");
      const mail = el("a", null, e.email); mail.href = `mailto:${e.email}?subject=${encodeURIComponent("Goal Engine: your enquiry")}`; acts.append(mail);
      if (e.phone) {
        const digits = e.phone.replace(/[^\d]/g, "");
        const wa = el("a", "wa", "WhatsApp"); wa.href = `https://wa.me/${digits.length === 10 ? "91" + digits : digits}`; wa.target = "_blank"; wa.rel = "noopener"; acts.append(wa);
        const call = el("a", null, e.phone); call.href = `tel:${e.phone}`; acts.append(call);
      }
      const sel = el("select"); sel.setAttribute("aria-label", "Status");
      for (const s of d.statuses) { const o = el("option", null, ENQ[s] || s); o.value = s; o.selected = s === e.status; sel.append(o); }
      const saved = el("span", "saved");
      const save = async (body) => { await api(`/api/admin/enquiries/${e.id}`, body); saved.textContent = "Saved"; setTimeout(() => { saved.textContent = ""; }, 1500); };
      sel.addEventListener("change", () => save({ status: sel.value }).catch((x) => toast(x.message)));
      const del = btn("ghost small", "Delete (they asked)", async () => {
        if (!confirm(`Delete ${e.name}'s enquiry for good?`)) return;
        await api(`/api/admin/enquiries/${e.id}`, undefined, "DELETE"); toast("Deleted"); enquiries(box);
      });
      acts.append(sel, saved, el("span", "grow"), del);
      const note = el("textarea"); note.placeholder = "Your note (only you see this)"; note.value = e.note || "";
      note.addEventListener("change", () => save({ note: note.value }).catch((x) => toast(x.message)));
      c.append(top, tags); if (e.message) c.append(el("p", "msg", e.message)); c.append(acts, note);
      return c;
    });
    box.replaceChildren(chips, share, ...(list.length ? list : [el("p", "empty", "Nothing here yet.")]));
  }

  // ---------------------------------------------------------------- people
  let peopleFilter = "";
  async function people(box) {
    const { users } = await api("/api/admin/users");
    $("sub").textContent = `${users.length} accounts`;
    const search = el("input"); search.type = "search"; search.placeholder = "Filter by name, email, city or work"; search.value = peopleFilter;
    const wrap = el("div", "tablewrap");
    const draw = () => {
      const f = peopleFilter.toLowerCase();
      const rows = users.filter((u) => !f || [u.name, u.email, u.username, u.location, u.why].join(" ").toLowerCase().includes(f));
      const t = el("table"); const h = el("tr");
      for (const x of ["Person", "Contact", "Work", "Uses", "Messages", "Joined", "Last seen"]) h.append(el("th", null, x));
      t.append(h);
      for (const u of rows) {
        const tr = el("tr");
        const who = el("td"); who.append(el("b", null, u.name), el("div", "muted", `@${u.username}${u.location ? " · " + u.location : ""}`));
        if (!u.verified) who.append(el("span", "tag", "email not verified"));
        const contact = el("td"); const m = el("a", null, u.email); m.href = `mailto:${u.email}`; contact.append(m, el("div", "muted", u.phone));
        const uses = el("td");
        for (const [k, n] of Object.entries(u.areas)) uses.append(el("span", "tag ok", `${k} ${n}`));
        if (u.connections) uses.append(el("span", "tag", `${u.connections} db connects`));
        if (!uses.childElementCount) uses.append(el("span", "muted", "chat only"));
        tr.append(who, contact, el("td", null, u.why || "—"), uses, el("td", "n", u.messages), el("td", null, when(u.joined)), el("td", null, when(u.last_seen)));
        t.append(tr);
      }
      wrap.replaceChildren(rows.length ? t : el("p", "empty", "No one matches."));
    };
    search.addEventListener("input", () => { peopleFilter = search.value; draw(); });
    draw();
    const top = el("div", "chips"); top.append(search);
    box.replaceChildren(top, panel("Everyone who made a free account", "Guests without an account are counted on the overview only.", wrap));
  }

  // ---------------------------------------------------------------- expert requests (unchanged API)
  let expStatus = "new";
  const EXP = { "": "All", new: "New", talking: "Talking", done: "Done", spam: "Spam" };
  async function experts(box) {
    const { requests } = await api(`/api/admin/requests?status=${encodeURIComponent(expStatus)}`);
    $("sub").textContent = `${requests.length} ${EXP[expStatus].toLowerCase()}`;
    if (expStatus === "new") badge("bExp", requests.length);
    const chips = el("div", "chips");
    for (const [k, v] of Object.entries(EXP)) { const b = btn(null, v, () => { expStatus = k; experts(box); }); b.setAttribute("aria-pressed", String(k === expStatus)); chips.append(b); }
    const list = requests.map((q) => {
      const c = el("article", `card ${q.status}`);
      const top = el("div", "top"); top.append(el("b", null, q.topic), el("span", "when", when(q.created_at)));
      const who = el("p", "muted"); who.append(el("b", null, q.name), ` · wants ${q.contact}${q.intent ? " · " + q.intent : ""}`);
      const acts = el("div", "acts");
      if (q.whatsapp) { const a = el("a", "wa", "WhatsApp"); a.href = q.whatsapp; a.target = "_blank"; a.rel = "noopener"; acts.append(a); }
      if (q.phone) { const a = el("a", null, q.phone); a.href = `tel:${q.phone}`; acts.append(a); }
      if (q.email) { const a = el("a", null, q.email); a.href = `mailto:${q.email}?subject=${encodeURIComponent("Goal Engine: " + q.topic)}`; acts.append(a); }
      const sel = el("select"); sel.setAttribute("aria-label", "Status");
      for (const s of ["new", "talking", "done", "spam"]) { const o = el("option", null, EXP[s]); o.value = s; o.selected = s === q.status; sel.append(o); }
      const saved = el("span", "saved");
      const save = async (body) => { await api(`/api/admin/requests/${q.id}`, body); saved.textContent = "Saved"; setTimeout(() => { saved.textContent = ""; }, 1500); };
      sel.addEventListener("change", () => save({ status: sel.value }).catch((x) => toast(x.message)));
      acts.append(sel, saved);
      const note = el("textarea"); note.placeholder = "Your note (only you see this)"; note.value = q.note || "";
      note.addEventListener("change", () => save({ note: note.value }).catch((x) => toast(x.message)));
      c.append(top, el("p", "msg", q.need), who, acts, note);
      return c;
    });
    box.replaceChildren(chips, ...(list.length ? list : [el("p", "empty", "Nothing here.")]));
  }

  // ---------------------------------------------------------------- DBaba knowledge
  function renderBlock(b) {
    const d = el("div", "blk");
    if (b.title) d.append(el("h3", null, b.title));
    if (b.type === "findings") {
      const ul = el("ul");
      for (const f of b.items || []) {
        const li = el("li"); li.append(el("span", "kind", f.kind === "check" ? "to check" : f.kind), el("b", null, f.title));
        if (f.evidence) li.append(el("pre", null, f.evidence));
        if (f.fix) li.append(el("div", null, f.fix));
        ul.append(li);
      }
      d.append(ul);
    } else if (b.type === "sql" || b.type === "table") {
      d.append(el("pre", null, b.sql || ""));
    } else {
      d.append(el("pre", null, b.body || ""));
    }
    return d;
  }

  let tryText = "";
  async function dbaba(box) {
    const d = await api("/api/admin/knowledge");
    const k = d.dbaba, cur = d.curriculum;
    $("sub").textContent = `${cur.done} of ${cur.total} nights done`;
    const tiles = el("div", "tiles");
    tiles.append(
      tile("Concepts it can explain", k.concepts, `${k.phrasings} ways of asking (English, Hinglish, Hindi)`),
      tile("Fundamentals", k.query_fundamentals, `${k.server_causes} server causes`),
      tile("SQL checkers", k.checkers.length, k.checkers.join(", ")),
      tile("Practice banks", k.practice_nights, "nights with lab-proven tests"),
      tile("Said wrong by users", d.said_wrong.length, "waiting to be learned", d.said_wrong.length > 0),
      tile("Sheetu issues", d.sheetu.issues, `${d.sheetu.formulas} formula notes`),
    );

    const pr = el("div", "progress"); const bar = el("span"); bar.style.width = `${cur.total ? (100 * cur.done) / cur.total : 0}%`; pr.append(bar);
    const nights = el("div", "nights");
    for (const n of cur.nights) {
      const c = el("div", `night${n.done ? " done" : ""}`); const t = el("div"); t.append(el("span", null, n.title));
      t.append(el("span", "d", n.done ? `done ${n.date}` : "to do")); c.append(el("span", "no", n.n), t); nights.append(c);
    }

    const et = el("table"); const eh = el("tr");
    for (const x of ["Database", "Lab-tested", "Error codes", "Fixes", "Query rules", "Live checks / notes"]) eh.append(el("th", null, x));
    et.append(eh);
    for (const e of k.engines) {
      const tr = el("tr"); tr.append(el("td", null, e.name), el("td", null, e.tested ? "yes" : "notes only"), el("td", "n", e.error_codes),
                                     el("td", "n", e.fixes), el("td", "n", e.query_rules), el("td", "n", e.live_checks || e.notes));
      et.append(tr);
    }

    // test box
    const tryBox = el("div", "try");
    const ta = el("textarea"); ta.placeholder = "Ask exactly what a developer would: an error, a query, code with a query, or a question (e.g. left join aur inner join mein kya farak hai)";
    ta.value = tryText; ta.addEventListener("input", () => { tryText = ta.value; });
    const out = el("div", "answer");
    const go = async () => {
      if (!ta.value.trim()) return;
      out.replaceChildren(el("p", "muted", "Thinking…"));
      try {
        const r = await api("/api/admin/knowledge/try", { text: ta.value });
        out.replaceChildren(el("p", "muted", `Database: ${r.engine || "not named or shown"}`), ...r.blocks.map(renderBlock));
      } catch (x) { out.replaceChildren(el("p", "empty", x.message)); }
    };
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(); } });
    const row = el("div", "row"); row.append(btn("btn", "Ask DBaba", go), el("span", "muted", "Ctrl ↵ · nothing typed here is kept"));
    tryBox.append(ta, row, out);

    // what users said was wrong
    const misses = el("div");
    if (!d.said_wrong.length) misses.append(el("p", "empty", "No one has said an answer was wrong."));
    for (const m of d.said_wrong) {
      const c = el("div", "card new");
      const top = el("div", "top"); top.append(el("b", null, m.said || "(said it was wrong)"), el("span", "tag", m.agent), el("span", "tag warn", `${m.count}×`), el("span", "when", when(m.last_seen)));
      c.append(top, el("pre", "detail", m.detail));
      if (m.answer) c.append(el("p", "muted", `It answered: ${m.answer}`));
      const acts = el("div", "acts");
      const set = (status) => async () => { await api(`/api/admin/knowledge/miss/${m.id}`, { status }); toast(status === "learned" ? "Marked learned" : "Ignored"); dbaba(box); };
      acts.append(btn("ghost small", "Try it", () => { tryText = m.detail; ta.value = m.detail; ta.focus(); ta.scrollIntoView({ block: "center" }); }),
                  btn("ghost small", "Learned", set("learned")), btn("ghost small", "Ignore", set("ignored")));
      c.append(acts);
      misses.append(c);
    }

    const g1 = el("div", "grid2");
    g1.append(panel("Test DBaba", "Its answer exactly as the developer workspace gives it, without a database connected.", tryBox),
              panel("Curriculum", "From docs/dbaba-curriculum.md. Each night is lab-proven on MariaDB and PostgreSQL.", pr, nights));
    const g2 = el("div", "grid2");
    const ew = el("div", "tablewrap"); ew.append(et);
    g2.append(panel("Per database", "What each database's own file knows. SQL Server, Oracle and SQLite are notes until lab-tested.", ew),
              panel("Answers users said were wrong", "Values are hidden before they are saved. The nightly learning reads these first.", misses));
    box.replaceChildren(tiles, g1, g2);
  }

  // ---------------------------------------------------------------- learning (unchanged API)
  async function learning(box) {
    const { requests, learned } = await api("/api/admin/learning?status=new");
    $("sub").textContent = `${requests.length} to learn`;
    const set = (kind, id, status) => async () => { await api(`/api/admin/learning/${kind}/${id}`, { status }); learning(box); };
    const row = (text, meta, buttons) => {
      const c = el("div", "card"); const top = el("div", "top"); top.append(el("b", null, text)); c.append(top, el("p", "muted", meta));
      const acts = el("div", "acts"); for (const [l, fn] of buttons) acts.append(btn("ghost small", l, fn)); c.append(acts); return c;
    };
    const a = [el("p", "muted", "Sentences people asked an agent to learn (\"None of these\"), most asked first. The nightly learning reads them too.")];
    if (!requests.length) a.push(el("p", "empty", "Nothing waiting."));
    for (const q of requests) a.push(row(q.text, `${q.brain} · asked ${q.count}× · best guess ${q.guess || "none"}`,
      [["Learned", set("request", q.id, "learned")], ["Ignore", set("request", q.id, "ignored")]]));
    const b = [el("p", "muted", "Sentences learned from three or more people agreeing. Reject anything wrong.")];
    if (!learned.length) b.push(el("p", "empty", "Nothing yet."));
    for (const p of learned) b.push(row(p.text, `${p.brain} → ${p.label} · ${p.status}`,
      p.status === "rejected" ? [["Allow", set("phrase", p.id, "approved")]] : [["Reject", set("phrase", p.id, "rejected")]]));
    const g = el("div", "grid2"); g.append(panel("Asked to learn", null, ...a), panel("Learned from users", null, ...b));
    box.replaceChildren(g);
  }

  // first load: badges from the overview, then the page in the address
  api("/api/admin/overview").then((d) => { badge("bEnq", d.enquiries.new); badge("bExp", d.experts_new); badge("bLearn", d.learning.to_learn + d.learning.said_wrong); }).catch(() => {});
  show(location.hash.slice(1) || "overview");
})();
