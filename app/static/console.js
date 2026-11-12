/* DBaba workspace (app/console.py): log in -> open the database area -> ask DBaba anything; connect a database to have
   the answers checked on it and to run queries. What is asked, the queries and their rows are only ever on this screen;
   query history lives in this browser tab (sessionStorage), nowhere else. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; };
  const btn = (cls, text, fn) => { const b = el("button", cls, text); b.type = "button"; if (fn) b.addEventListener("click", fn); return b; };
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const KEY = MAC ? "⌘ ↵" : "Ctrl ↵";
  const HISTORY_KEY = "ge_console_history";
  const store = {                       // per-viewer conveniences only; the page works without them
    get(k, d) { try { const v = sessionStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (_) { return d; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } },
  };

  // ---------------------------------------------------------------- theme
  const root = document.documentElement;
  try { if (localStorage.getItem("ge_console_theme") === "light") root.dataset.theme = "light"; } catch (_) { /* ignore */ }
  $("theme").addEventListener("click", () => {
    const light = root.dataset.theme !== "light";
    if (light) root.dataset.theme = "light"; else delete root.dataset.theme;
    try { localStorage.setItem("ge_console_theme", light ? "light" : "dark"); } catch (_) { /* ignore */ }
  });

  // ---------------------------------------------------------------- talking to Goal Engine
  async function api(path, body) {
    const res = await fetch(path, { method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
                                    headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) {
      const e = new Error(data.error || data.detail || "Something went wrong. Try again.");
      e.status = res.status; e.data = data;
      if (res.status === 410 || res.status === 409) dbGone(data.reason || "none");
      else if (res.status === 403 && data.db_area) { stopClock(); conn = null; showGate(); }
      throw e;
    }
    return data;
  }
  let toastTimer = null;
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 1800);
  }
  async function copyText(text, what) {
    try { await navigator.clipboard.writeText(text); toast(what || "Copied"); }
    catch (_) { toast("Couldn't copy: your browser blocked it"); }
  }

  // ---------------------------------------------------------------- gate: account and database area
  const gate = $("gate"), card = $("gateCard"), ws = $("ws");
  let conn = null;                      // the connected database, or null
  let lastForm = null;                  // the last connect details, without the password (memory only)

  function setCard(...nodes) { card.replaceChildren(...nodes); gate.hidden = false; ws.hidden = true; }

  async function showGate() {
    for (const id of ["conn", "connectBtn", "disconnect", "idle", "warnbar"]) $(id).hidden = true;
    let me = null;
    try { me = (await api("/api/account/me")).user; } catch (_) { /* treated as logged out */ }
    if (!me) {
      const a = el("a", "primary", "Log in on Goal Engine"); a.href = "/";
      return setCard(el("h1", null, "Log in first"), el("p", "muted", "The DBaba workspace opens inside your account, so only you can see it."), a);
    }
    if (!me.email_verified) {
      const a = el("a", "primary", "Confirm your email"); a.href = "/";
      return setCard(el("h1", null, "Confirm your email first"), el("p", "muted", "Then come back here."), a);
    }
    let st;
    try { st = await api("/api/db-area"); } catch (ex) { return setCard(el("p", "err", ex.message)); }
    if (st.state === "soon") return setCard(el("h1", null, "Opening soon"), el("p", "muted", "The DBaba workspace opens in a few days."));
    if (st.state !== "open") return unlockCard();
    let cs = {};
    try { cs = await api("/api/console/state"); } catch (_) { /* not connected */ }
    openWorkspace(cs.connected ? cs : null);
  }

  function unlockCard() {
    const send = btn("primary", "Email me a code");
    const note = el("p", "muted", "This is a private area. Each time you open it, we email you a fresh code, so nobody else can use it with your account.");
    const holder = el("div");
    send.addEventListener("click", async () => {
      send.disabled = true;
      try {
        const d = await api("/api/db-area/code", {});
        note.textContent = `Code sent to ${d.email}. It works for 10 minutes.`;
        send.hidden = true;
        const form = el("form", "form");
        const code = el("input"); code.inputMode = "numeric"; code.autocomplete = "one-time-code"; code.maxLength = 8; code.required = true;
        const lab = el("label", "f"); lab.append(el("span", null, "Code"), code);
        const go = el("button", "primary", "Open"); go.type = "submit";
        const err = el("p", "err"); err.hidden = true;
        form.append(lab, go, err);
        form.addEventListener("submit", async (e) => {
          e.preventDefault(); go.disabled = true; err.hidden = true;
          try { await api("/api/db-area/open", { code: code.value.trim() }); showGate(); }
          catch (ex) { err.textContent = ex.message; err.hidden = false; go.disabled = false; }
        });
        holder.replaceChildren(form); code.focus();
      } catch (ex) { note.textContent = ex.message; send.disabled = false; }
    });
    setCard(el("h1", null, "Your DBaba workspace is locked"), note, send, holder);
  }

  // ---------------------------------------------------------------- connect a database (optional)
  function connectForm(reason, onDone) {
    const nodes = [el("h1", null, "Connect a database")];
    if (reason === "timed_out") nodes.push(el("p", "reason", "Disconnected after 10 minutes without typing. The details were wiped. Enter the password to connect again."));
    nodes.push(el("p", "muted", "MySQL, MariaDB or PostgreSQL. DBaba then checks its answers on your database, read-only, and you can run queries."));
    const form = el("form", "form");
    const field = (label, input) => { const l = el("label", "f"); l.append(el("span", null, label), input); return l; };
    const type = el("select");
    for (const [v, t] of [["mysql", "MySQL / MariaDB"], ["postgres", "PostgreSQL"]]) { const o = el("option", null, t); o.value = v; type.append(o); }
    const host = el("input"); host.required = true; host.autocomplete = "off"; host.spellcheck = false; host.placeholder = "db.example.com";
    const port = el("input"); port.inputMode = "numeric"; port.maxLength = 5;
    const dbn = el("input"); dbn.required = true; dbn.autocomplete = "off"; dbn.spellcheck = false;
    const user = el("input"); user.required = true; user.autocomplete = "off"; user.spellcheck = false;
    const pass = el("input"); pass.type = "password"; pass.autocomplete = "new-password";
    if (lastForm) { type.value = lastForm.type; host.value = lastForm.host; port.value = lastForm.port; dbn.value = lastForm.database; user.value = lastForm.user; }
    const ph = () => { port.placeholder = type.value === "postgres" ? "5432" : "3306"; };
    type.addEventListener("change", ph); ph();
    const r1 = el("div", "two"); r1.append(field("Database type", type), field("Port", port));
    const r2 = el("div", "two"); r2.append(field("Database name", dbn), el("span"));
    const go = el("button", "primary", "Connect"); go.type = "submit";
    const err = el("p", "err"); err.hidden = true;
    const ipNote = el("p", "fine", "Tip: a database user that can only SELECT is the safest choice.");
    api("/api/connector-info").then((d) => {
      if (!d.server_ip) return;
      ipNote.replaceChildren(document.createTextNode("Your database must accept connections from Goal Engine's server: "),
        el("span", "ip", d.server_ip), document.createTextNode(" (cPanel: Remote MySQL; cloud: allowed IPs). A SELECT-only user is safest."));
    }).catch(() => {});
    form.append(r1, field("Host", host), r2, field("User", user), field("Password", pass), go, err, ipNote);
    form.addEventListener("submit", async (e) => {
      e.preventDefault(); go.disabled = true; go.textContent = "Connecting…"; err.hidden = true;
      const details = { type: type.value, host: host.value.trim(), port: port.value.trim(), database: dbn.value.trim(), user: user.value.trim() };
      try {
        const d = await api("/api/console/connect", { ...details, password: pass.value });
        pass.value = "";
        lastForm = details;
        onDone(d);
      } catch (ex) { err.textContent = ex.message; err.hidden = false; go.disabled = false; go.textContent = "Connect"; }
    });
    nodes.push(form);
    setTimeout(() => (lastForm ? pass : host).focus(), 0);
    return nodes;
  }

  function openConnect(reason) {
    const modal = $("modal"), mc = $("modalCard");
    const close = btn("icon close", "×", () => { modal.hidden = true; });
    close.title = "Close";
    mc.replaceChildren(close, ...connectForm(reason, (d) => { modal.hidden = true; setConnected(d); toast("Database connected"); }));
    modal.hidden = false;
  }
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) $("modal").hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("modal").hidden = true; });
  for (const id of ["connectBtn", "sideConnect", "needConnect"]) $(id).addEventListener("click", () => openConnect());

  // ---------------------------------------------------------------- the workspace
  function openWorkspace(state) {
    gate.hidden = true; ws.hidden = false;
    $("runKey").textContent = KEY;
    setConnected(state);
    $("askText").focus();
  }

  function setConnected(state) {
    conn = state;
    const on = !!state;
    $("conn").hidden = !on; $("disconnect").hidden = !on; $("idle").hidden = !on; $("connectBtn").hidden = on;
    $("sideBody").hidden = !on; $("sideEmpty").hidden = on; $("refreshSchema").hidden = !on;
    $("needdb").hidden = on; $("sqlwrap").hidden = !on;
    $("askHint").textContent = `${KEY} to send · ` + (on ? `checks run on ${state.database} (read-only)` : "no database connected: answers are based on what you paste");
    if (!on) { stopClock(); $("warnbar").hidden = true; return; }
    $("connEngine").textContent = state.db_type === "postgres" ? "PostgreSQL" : "MySQL";
    $("connWhere").textContent = `${state.user ? state.user + "@" : ""}${state.host} / ${state.database}${state.version ? "  ·  " + state.version : ""}`;
    if (state.tables) renderTables(state.tables); else loadTables();
    setDeadline(state.seconds_left);
    startClock();
    lines();
  }

  function dbGone(reason) {
    if (!conn) return;
    setConnected(null);
    if (reason === "timed_out") { toast("Database disconnected after 10 minutes without typing"); openConnect("timed_out"); }
  }

  $("disconnect").addEventListener("click", async () => {
    try { await api("/api/console/disconnect", {}); } catch (_) { /* gone anyway */ }
    last = null;
    $("paneResult").replaceChildren(el("p", "hint", "Run a query to see its rows here."));
    setConnected(null);
    toast("Disconnected. The details were wiped.");
  });

  // modes
  function mode(which) {
    $("modeAsk").setAttribute("aria-selected", String(which === "ask"));
    $("modeSql").setAttribute("aria-selected", String(which === "sql"));
    $("ask").hidden = which !== "ask"; $("sqlmode").hidden = which !== "sql";
    (which === "ask" ? $("askText") : conn ? $("sql") : $("needConnect")).focus();
  }
  $("modeAsk").addEventListener("click", () => mode("ask"));
  $("modeSql").addEventListener("click", () => mode("sql"));

  // ---------------------------------------------------------------- Ask DBaba
  const thread = $("thread"), askText = $("askText");
  for (const c of $("chips").children) c.addEventListener("click", () => { askText.value = c.dataset.text; askText.focus(); });
  askText.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); $("composer").requestSubmit(); } });
  askText.addEventListener("input", typed);
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask(askText.value); });

  let asking = false;
  async function ask(text) {
    text = (text || "").trim();
    if (!text || asking) return;
    asking = true; $("askSend").disabled = true;
    $("welcome").hidden = true;
    mode("ask");
    thread.append(el("div", "q", text));
    const a = el("div", "a");
    const head = el("div", "a-head"); head.append(el("span", "who", "DBaba"));
    const wait = el("div", "blk thinking", conn ? `Reading it, and checking on ${conn.database}…` : "Reading it…");
    a.append(head, wait);
    thread.append(a);
    a.scrollIntoView({ block: "start", behavior: "smooth" });
    askText.value = "";
    try {
      const r = await api("/api/console/ask", { text });
      if (typeof r.seconds_left === "number") setDeadline(r.seconds_left);
      wait.remove();
      const live = (r.blocks || []).some((b) => b.live || b.type === "table");
      head.append(el("span", live ? "badge" : "badge plain", live ? `Checked on ${conn ? conn.database : "your database"}` : r.connected ? "Read from what you pasted" : "Based on what you pasted"));
      for (const b of r.blocks || []) a.append(renderBlock(b));
      thread.scrollTop = a.offsetTop - thread.offsetTop - 12;      // the answer from its start
    } catch (ex) {
      wait.className = "blk"; wait.replaceChildren(el("p", "err", ex.message));
      askText.value = text;
    } finally {
      asking = false; $("askSend").disabled = false; askText.focus();
    }
  }

  const SQL_LINE = /^\s*(SELECT|WITH|ALTER|CREATE|SHOW|SET|UPDATE|DELETE|INSERT|EXPLAIN|DROP|GRANT|REVOKE|KILL|ANALYZE|VACUUM|OPTIMIZE|FLUSH|CALL|DO|BEGIN|COMMIT|ROLLBACK|START|PURGE|RESET|TRUNCATE|SELECT\s)\b/i;
  function codeBox(sql, opts = {}) {
    const wrap = el("div");
    wrap.append(el("pre", "code", sql));
    const acts = el("div", "acts");
    acts.append(btn("tb small", "Copy", () => copyText(sql, "Copied")));
    acts.append(btn("tb small", "Put in the SQL editor", () => { if (!conn) { openConnect(); return; } mode("sql"); setQuery(sql); }));
    if (opts.run && conn && /^\s*(select|with|show|describe|desc|explain)\b/i.test(sql)) {
      acts.append(btn("tb small", "Run it", () => { mode("sql"); setQuery(sql); runCurrent(false); }));
    }
    wrap.append(acts);
    return wrap;
  }

  function textBody(body) {
    const frag = document.createDocumentFragment();
    const ls = String(body || "").split("\n");
    let code = [];
    const flush = () => { if (code.length) { frag.append(codeBox(code.join("\n").trim())); code = []; } };
    for (const raw of ls) {
      const line = raw.replace(/\s+$/, "");
      if (SQL_LINE.test(line) || (code.length && /^\s+\S/.test(line) && !/^\s*[-•]/.test(line))) { code.push(line); continue; }
      flush();
      if (!line.trim()) { frag.append(el("div", "gap")); continue; }
      const li = line.match(/^\s*[-•]\s+(.*)$/);
      if (li) frag.append(el("div", "li", li[1]));
      else frag.append(el("div", "ln", line));
    }
    flush();
    return frag;
  }

  function renderBlock(b) {
    const box = el("div", "blk");
    if (b.type === "text") {
      if (b.folded) {
        const d = el("details"); d.append(el("summary", null, b.title)); d.append(textBody(b.body)); box.append(d);
      } else {
        if (b.title) box.append(el("h3", null, b.title));
        box.append(textBody(b.body));
      }
    } else if (b.type === "findings") {
      box.append(el("h3", null, b.title));
      if (b.items && b.items.length) {
        const ul = el("ul", "findings");
        for (const f of b.items) {
          const li = el("li", f.kind);
          const h = el("div");
          h.append(el("span", "kind", f.kind === "check" ? "to check" : f.kind === "speed" ? "speed" : f.kind), el("b", null, f.title));
          li.append(h);
          if (f.evidence) li.append(el("div", "ev", f.evidence));
          if (f.fix) li.append(el("div", null, f.fix));
          if (f.basis) li.append(el("div", "basis", `Why: ${f.basis}`));
          ul.append(li);
        }
        box.append(ul);
      }
    } else if (b.type === "sql") {
      box.append(el("h3", null, b.title), codeBox(b.sql, { run: b.run }));
    } else if (b.type === "table") {
      box.append(el("h3", null, b.title ? `Checked: ${b.title}` : "Checked on your database"));
      box.append(el("pre", "code", b.sql));
      if (b.error) box.append(el("div", "msg", b.error));
      else {
        const tw = el("div", "tablewrap");
        tw.append(gridTable(b.columns, b.rows, []));
        box.append(tw);
        if (!b.rows.length) box.append(el("p", "hint", "No rows."));
      }
    }
    return box;
  }

  // ---------------------------------------------------------------- tables sidebar
  let allTables = [];
  async function loadTables() {
    $("tables").replaceChildren(el("li", "side-note", "Loading…"));
    try { renderTables((await api("/api/console/schema")).tables); }
    catch (ex) { $("tables").replaceChildren(el("li", "side-note", ex.message)); }
  }
  function renderTables(tables) { allTables = tables || []; filterTables(); }
  function filterTables() {
    const f = $("filter").value.trim().toLowerCase();
    const list = $("tables");
    list.replaceChildren();
    const shown = allTables.filter((t) => !f || t.name.toLowerCase().includes(f));
    if (!shown.length) { list.append(el("li", "side-note", allTables.length ? "No table matches." : "No tables.")); return; }
    for (const t of shown) {
      const li = el("li"), d = el("details"), s = el("summary");
      const q = btn("q", "SELECT", (e) => { e.preventDefault(); e.stopPropagation(); mode("sql"); setQuery(`SELECT *\nFROM ${ident(t.name)}\nLIMIT 100;`); });
      q.title = `Query ${t.name}`;
      s.append(el("span", "tname", t.name), el("span", "trows", t.rows ? Number(t.rows).toLocaleString("en-IN") : ""), q);
      d.append(s);
      for (const c of t.columns || []) {
        const row = el("div", "col");
        row.append(el("span", null, c.name));
        if (c.key) row.append(el("span", "k", c.key === "PRI" || /primary/i.test(c.key) ? "PK" : c.key.slice(0, 3)));
        row.append(el("span", "ty", c.type));
        row.title = `Insert ${c.name}`;
        row.addEventListener("click", () => { if ($("ask").hidden) insertAtCursor(ident(c.name)); else { askText.setRangeText(c.name, askText.selectionStart, askText.selectionEnd, "end"); askText.focus(); } });
        d.append(row);
      }
      li.append(d);
      list.append(li);
    }
  }
  function ident(name) {
    if (/^[a-z_][a-z0-9_$]*$/.test(name)) return name;
    return conn && conn.db_type === "postgres" ? `"${name.replace(/"/g, '""')}"` : `\`${name.replace(/`/g, "``")}\``;
  }
  $("filter").addEventListener("input", filterTables);
  $("refreshSchema").addEventListener("click", loadTables);

  // ---------------------------------------------------------------- SQL editor
  const sql = $("sql"), gutter = $("gutter");
  function lines() {
    const n = sql.value.split("\n").length;
    let s = "";
    for (let i = 1; i <= n; i++) s += i + "\n";
    gutter.textContent = s;
    gutter.scrollTop = sql.scrollTop;
  }
  sql.addEventListener("input", () => { lines(); typed(); });
  sql.addEventListener("scroll", () => { gutter.scrollTop = sql.scrollTop; });
  sql.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runCurrent(false); return; }
    if (e.key === "Tab" && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); if (!e.shiftKey) insertAtCursor("  "); }
  });
  function insertAtCursor(text) {
    sql.focus();
    sql.setRangeText(text, sql.selectionStart, sql.selectionEnd, "end");
    lines(); typed();
  }
  function setQuery(text) { sql.value = text; sql.focus(); sql.setSelectionRange(text.length, text.length); lines(); typed(); }

  // the statement to run: the selected text, else the one the cursor is in (split at ';' outside quotes and comments)
  function statements(text) {
    const out = []; let start = 0, i = 0, q = null;
    while (i < text.length) {
      const c = text[i], n = text[i + 1];
      if (q) {
        if (q === "--" || q === "#") { if (c === "\n") q = null; }
        else if (q === "/*") { if (c === "*" && n === "/") { q = null; i++; } }
        else if (c === "\\" && q !== "`") i++;
        else if (c === q) q = null;
      } else if (c === "'" || c === '"' || c === "`") q = c;
      else if (c === "-" && n === "-") q = "--";
      else if (c === "#" && !(conn && conn.db_type === "postgres")) q = "#";
      else if (c === "/" && n === "*") { q = "/*"; i++; }
      else if (c === ";") { out.push([start, i]); start = i + 1; }
      i++;
    }
    out.push([start, text.length]);
    return out;
  }
  function currentStatement() {
    const v = sql.value;
    if (sql.selectionEnd > sql.selectionStart) return v.slice(sql.selectionStart, sql.selectionEnd).trim();
    const pos = sql.selectionStart;
    const parts = statements(v);
    let idx = parts.findIndex(([a, b]) => pos >= a && pos <= b + 1);
    if (idx < 0) idx = parts.length - 1;
    while (idx > 0 && !v.slice(parts[idx][0], parts[idx][1]).trim()) idx--;
    return v.slice(parts[idx][0], parts[idx][1]).trim();
  }

  let last = null, busy = false;
  function status(text, bad) { const s = $("status"); s.textContent = text || ""; s.classList.toggle("bad", !!bad); }

  async function runCurrent(explain) {
    let q = currentStatement();
    if (!q) { status("Type a query first.", true); return; }
    if (explain && !/^\s*explain\b/i.test(q)) q = "EXPLAIN " + q;
    if (busy) return;
    busy = true; $("run").disabled = true; $("explain").disabled = true;
    status("Running…");
    const started = performance.now();
    try {
      const r = await api("/api/console/run", { sql: q });
      setDeadline(r.seconds_left);
      last = r;
      renderResult(r);
      const more = r.limited ? ` · first ${r.row_count.toLocaleString("en-IN")} shown` : "";
      status(`${r.row_count.toLocaleString("en-IN")} row${r.row_count === 1 ? "" : "s"} · ${r.ms} ms${more}`);
      remember(q, true, r.row_count, r.ms);
    } catch (ex) {
      if ([403, 409, 410].includes(ex.status)) return;
      last = null;
      const pane = $("paneResult");
      pane.replaceChildren(el("div", "msg", ex.message));
      const why = btn("tb small", "Ask DBaba about this error", () => ask(`${q}\n\n${ex.message}`));
      const holder = el("div", "acts"); holder.append(why);
      holder.classList.add("pad");
      pane.append(holder);
      if (ex.data && ex.data.reason === "not_read_only") pane.append(el("p", "hint", "Changes to data aren't run here. Use the database agent's “Change data” step: preview first, then Apply, with Undo."));
      status(`Failed · ${Math.round(performance.now() - started)} ms`, true);
      $("copyBox").hidden = true;
      remember(q, false, null, null);
    } finally {
      busy = false; $("run").disabled = false; $("explain").disabled = false;
    }
  }
  $("run").addEventListener("click", () => runCurrent(false));
  $("explain").addEventListener("click", () => runCurrent(true));
  $("check").addEventListener("click", () => { const q = currentStatement(); if (q) ask(q); else status("Type a query first.", true); });
  $("clear").addEventListener("click", () => setQuery(""));

  const NUM = /^-?\d+(\.\d+)?(e[+-]?\d+)?$/i;
  function gridTable(columns, rows, hidden) {
    const table = el("table", "grid");
    const thead = el("thead"), htr = el("tr");
    htr.append(el("th", "rn", "#"));
    for (const c of columns) htr.append(el("th", null, c));
    thead.append(htr);
    const tb = el("tbody");
    const masked = new Set(hidden || []);
    rows.forEach((row, i) => {
      const tr = el("tr");
      tr.append(el("td", "rn", String(i + 1)));
      row.forEach((v, j) => {
        const isNull = v === null;
        const td = el("td", isNull ? "null" : masked.has(columns[j]) ? "masked" : (typeof v === "number" || NUM.test(String(v))) ? "num" : null,
                      isNull ? "NULL" : String(v));
        if (!isNull && String(v).length > 60) td.title = String(v).slice(0, 2000);
        tr.append(td);
      });
      tb.append(tr);
    });
    table.append(thead, tb);
    return table;
  }
  function renderResult(r) {
    const pane = $("paneResult");
    if (!r.columns.length) { pane.replaceChildren(el("div", "msg info", "Done. The statement returned no columns.")); $("copyBox").hidden = true; return; }
    pane.replaceChildren(gridTable(r.columns, r.rows, r.hidden_columns));
    if ((r.hidden_columns || []).length) pane.append(el("p", "hint", `Hidden for safety: ${r.hidden_columns.join(", ")} (columns that hold passwords or tokens).`));
    $("copyBox").hidden = false;
  }
  $("paneResult").addEventListener("click", (e) => {
    const td = e.target.closest("td");
    if (!td || td.classList.contains("rn") || !last) return;
    const tr = td.parentElement, i = [...tr.parentElement.children].indexOf(tr), j = [...tr.children].indexOf(td) - 1;
    const v = last.rows[i] ? last.rows[i][j] : null;
    copyText(v === null ? "NULL" : String(v), "Value copied");
    td.classList.add("copied"); setTimeout(() => td.classList.remove("copied"), 600);
  });
  function asTable(sep, quote) {
    const cell = (v) => {
      if (v === null) return quote ? "" : "NULL";
      const s = String(v);
      if (quote && /[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
      return sep === "\t" ? s.replace(/[\t\n\r]+/g, " ") : s;
    };
    return [last.columns.map(cell).join(sep), ...last.rows.map((r) => r.map(cell).join(sep))].join("\n");
  }
  $("copyBox").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-copy]");
    if (!b || !last) return;
    if (b.dataset.copy === "tsv") copyText(asTable("\t", false), `${last.rows.length} rows copied: paste into Excel or Sheets`);
    else if (b.dataset.copy === "csv") copyText(asTable(",", true), "CSV copied");
    else copyText(JSON.stringify(last.rows.map((r) => Object.fromEntries(last.columns.map((c, i) => [c, r[i]]))), null, 2), "JSON copied");
  });

  // history: this tab only
  function remember(q, ok, rows, ms) {
    const h = store.get(HISTORY_KEY, []);
    if (h.length && h[0].sql === q) h.shift();
    h.unshift({ sql: q, ok, rows, ms, at: Date.now() });
    store.set(HISTORY_KEY, h.slice(0, 50));
    if (!$("history").hidden) renderHistory();
  }
  function renderHistory() {
    const list = $("historyList"); list.replaceChildren();
    const h = store.get(HISTORY_KEY, []);
    if (!h.length) { list.append(el("li", "side-note", "Nothing yet.")); return; }
    for (const it of h) {
      const li = el("li");
      const when = new Date(it.at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
      li.append(el("div", "hq", it.sql), el("div", "hm", `${when} · ${it.ok ? `${it.rows} rows · ${it.ms} ms` : "failed"}`));
      li.title = "Put it in the editor";
      li.addEventListener("click", () => setQuery(it.sql));
      list.append(li);
    }
  }
  $("historyBtn").addEventListener("click", () => {
    const h = $("history"); h.hidden = !h.hidden; ws.classList.toggle("with-history", !h.hidden);
    if (!h.hidden) renderHistory();
  });
  $("historyClose").addEventListener("click", () => { $("history").hidden = true; ws.classList.remove("with-history"); });

  // resize editor / results
  (() => {
    const split = $("split"), wrap = $("sqlwrap");
    let dragging = false;
    split.addEventListener("pointerdown", (e) => { dragging = true; split.setPointerCapture(e.pointerId); });
    split.addEventListener("pointerup", () => { dragging = false; });
    split.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const r = wrap.getBoundingClientRect();
      wrap.style.setProperty("--editor-h", `${Math.min(80, Math.max(15, ((e.clientY - r.top - 44) / r.height) * 100))}%`);
    });
  })();

  // ---------------------------------------------------------------- idle: 10 minutes without typing or running -> database disconnected
  let deadline = 0, clock = null, lastAlive = 0;
  function setDeadline(seconds) { if (typeof seconds === "number") deadline = Date.now() + seconds * 1000; }
  function stopClock() { clearInterval(clock); clock = null; }
  function startClock() {
    stopClock();
    clock = setInterval(() => {
      const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
      const idle = $("idle");
      idle.textContent = `Idle disconnect in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      idle.classList.toggle("soon", left <= 60);
      const warn = $("warnbar");
      if (left <= 60 && left > 0) { warn.hidden = false; $("warnText").textContent = `No typing for a while: the database disconnects in ${left} s.`; }
      else warn.hidden = true;
      if (left <= 0) {
        stopClock();
        api("/api/console/state").then((s) => { if (s.connected) { setDeadline(s.seconds_left); startClock(); } else dbGone("timed_out"); }).catch(() => {});
      }
    }, 1000);
  }
  async function keepAlive() {
    lastAlive = Date.now();
    try { const r = await api("/api/console/alive", {}); setDeadline(r.seconds_left); } catch (_) { /* handled by api() */ }
  }
  function typed() { if (conn && Date.now() - lastAlive > 45000) keepAlive(); }
  $("stay").addEventListener("click", keepAlive);

  showGate();
})();
