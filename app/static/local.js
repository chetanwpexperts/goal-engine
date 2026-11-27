// DBaba's own screen (S4): log in, the 3-step setup, then Chat / Report / Automation. Text from the server is always
// set with textContent, never as HTML.
(() => {
  "use strict";
  const app = document.getElementById("app");
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const btn = (text, cls, fn) => { const b = el("button", cls, text); b.type = "button"; if (fn) b.addEventListener("click", fn); return b; };
  const field = (label, name, value, type) => {
    const wrap = el("div"); const l = el("label", null, label); const i = el("input"); i.name = name; i.value = value ?? ""; i.type = type || "text";
    l.htmlFor = i.id = "f_" + name; wrap.append(l, i); return wrap;
  };
  async function api(path, opts = {}) {
    const res = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json" }, ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = null; try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) { const err = new Error((data && data.error) || `HTTP ${res.status}`); err.status = res.status; throw err; }
    return data;
  }
  const ENGINES = { mysql: ["MySQL", 3306], mariadb: ["MariaDB", 3306], postgresql: ["PostgreSQL", 5432] };
  let state = null, poll = null;

  // ---------------------------------------------------------------- start
  async function start() {
    try { state = await api("/api/state"); } catch (e) { if (e.status === 401) return login(); return fail(e); }
    document.getElementById("logout").hidden = false;
    if (!state.setup_done) return wizard(1, {});
    tabs("chat");
  }
  function fail(e) { app.replaceChildren(el("div", "card err", e.message || String(e))); }

  function login(msg) {
    const card = el("div", "card");
    const pw = field("Screen password", "password", "", "password");
    const out = el("p", "err", msg || "");
    const go = btn("Log in", null, async () => {
      go.disabled = true;
      try { await api("/api/login", { method: "POST", body: { password: pw.querySelector("input").value } }); start(); }
      catch (e) { out.textContent = e.status === 409 ? "No screen password yet: start setup from the DBaba desktop app or the installer." : e.message; go.disabled = false; }
    });
    pw.querySelector("input").addEventListener("keydown", (ev) => { if (ev.key === "Enter") go.click(); });
    card.append(el("h1", null, "Log in to DBaba"), el("p", "muted", "The password you chose when DBaba was installed."), pw, el("div", "row"), out);
    card.lastChild.previousSibling.append(go);
    app.replaceChildren(card);
    pw.querySelector("input").focus();
  }
  document.getElementById("logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST", body: {} }).catch(() => {}); location.reload(); });

  // ---------------------------------------------------------------- setup: 3 steps
  function steps(n) {
    const s = el("div", "steps");
    ["1. Which database", "2. Connect", "3. First scan"].forEach((t, i) => s.append(el("span", i + 1 === n ? "now" : null, t)));
    return s;
  }
  async function wizard(step, pick) {
    const card = el("div", "card");
    card.append(steps(step));
    if (step === 1) {
      card.append(el("h1", null, "Which database do you want DBaba to look after?"));
      // where it is: on this computer (or an address DBaba can reach), or on a server reached through SSH (S4b)
      const where = el("div", "row taps");
      const sshBox = el("div");
      const onSsh = pick.ssh !== undefined;
      where.append(btn("On this computer, or an address I can reach", onSsh ? "ghost" : "on", () => wizard(1, {})),
                   btn("On a server I reach by SSH", onSsh ? "on" : "ghost", () => wizard(1, { ssh: pick.ssh || null })));
      card.append(where);
      const taps = el("div", "row taps");
      const found = el("div");
      const engineTaps = (ssh) => {
        taps.replaceChildren();
        for (const [id, [name, port]] of Object.entries(ENGINES)) taps.append(btn(name, null, () => wizard(2, { engine: id, host: "127.0.0.1", port, ssh })));
        taps.append(btn("Find it for me", "ghost", async () => {
          found.replaceChildren(el("p", "muted", ssh ? "Looking on the SSH server…" : "Looking on this machine…"));
          try {
            const r = await api("/api/setup/detect", { method: "POST", body: ssh ? { ssh } : {} });
            if (r.reason) return found.replaceChildren(el("p", "err", r.reason));
            if (!r.found.length) return found.replaceChildren(el("p", null, (ssh ? "Nothing found on that server" : "Nothing found on this machine") + ". Pick the database above and type its address."));
            found.replaceChildren(el("p", null, "Found:"));
            for (const f of r.found) found.append(btn(f.how, "ghost", () => wizard(2, { engine: f.engine, host: f.host, port: f.port, ssh })));
          } catch (e) { found.replaceChildren(el("p", "err", e.message)); }
        }));
      };
      if (!onSsh) {
        engineTaps(undefined);
      } else if (pick.ssh && pick.ssh.trusted) {
        sshBox.append(el("p", "good", `SSH: ${pick.ssh.user}@${pick.ssh.host} (port ${pick.ssh.port}). Its key is accepted.`));
        const { trusted, ...ssh } = pick.ssh;
        engineTaps(ssh);
      } else {
        sshBox.append(el("p", "muted", "DBaba runs here and reaches the database through SSH: nothing is installed on the server. " +
          "It uses a key file or this computer's ssh-agent; it never asks for or keeps an SSH password."));
        const g = el("div", "grid2");
        const p0 = pick.ssh || {};
        g.append(field("SSH server (name or IP)", "host", p0.host || ""), field("SSH port", "port", p0.port || 22),
                 field("SSH user", "user", p0.user || ""), field("Private key file (full path; empty = ssh-agent)", "key_file", p0.key_file || ""));
        const out = el("div");
        const read = () => { const v = {}; for (const i of g.querySelectorAll("input")) v[i.name] = i.value.trim(); v.port = Number(v.port || 22); return v; };
        const check = btn("Check the server's key", null, async () => {
          const ssh = read(); out.replaceChildren(el("p", "muted", "Asking the server for its key…"));
          try {
            const r = await api("/api/setup/ssh", { method: "POST", body: { ssh } });
            if (!r.ok) return out.replaceChildren(el("p", "err", r.reason));
            if (r.trusted) return wizard(1, { ssh: { ...ssh, trusted: true } });
            out.replaceChildren(el("p", null, "First time on this server: compare this fingerprint with the one its administrator gives you " +
              "(on the server: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub). Accept it only if it is the same."));
            for (const k of r.keys) {
              const row = el("div", "row");
              row.append(el("code", null, `${k.type} ${k.fingerprint}`), btn("Yes, this is my server's key", "ghost", async () => {
                try {
                  const a = await api("/api/setup/ssh", { method: "POST", body: { ssh, accept: k.fingerprint } });
                  if (!a.ok) return out.append(el("p", "err", a.reason));
                  wizard(1, { ssh: { ...ssh, trusted: true } });
                } catch (e) { out.append(el("p", "err", e.message)); }
              }));
              out.append(row);
            }
          } catch (e) { out.replaceChildren(el("p", "err", e.message)); }
        });
        const row = el("div", "row"); row.append(check);
        sshBox.append(g, row, out);
      }
      card.append(sshBox, taps, found);
    } else if (step === 2) {
      const name = ENGINES[pick.engine][0];
      card.append(el("h1", null, `Connect to ${name}`),
        el("p", "muted", "Use a read-only user (SELECT rights only). DBaba never changes data without showing you first and asking."));
      const g = el("div", "grid2");
      if (pick.ssh) card.append(el("p", "muted", `Through SSH as ${pick.ssh.user}@${pick.ssh.host}: the host is the database's address as that server sees it (usually 127.0.0.1).`));
      g.append(field(pick.ssh ? "Database host (on the server)" : "Host", "host", pick.host), field("Port", "port", pick.port), field("Database name", "database", pick.database || ""),
               field("User", "user", pick.user || ""), field("Password", "password", "", "password"));
      card.append(g);
      const out = el("p");
      const read = () => { const v = {}; for (const i of g.querySelectorAll("input")) v[i.name] = i.value; v.port = Number(v.port); v.engine = pick.engine; if (pick.ssh) v.ssh = pick.ssh; return v; };
      const test = btn("Test connection", "ghost", async () => {
        out.className = "muted"; out.textContent = "Testing…";
        try {
          const r = await api("/api/setup/test", { method: "POST", body: read() });
          out.className = r.ok ? "good" : "err";
          out.textContent = r.ok ? `Connected: ${r.version}.${r.warning ? " " + r.warning : ""}` : r.reason;
          save.disabled = !r.ok;
        } catch (e) { out.className = "err"; out.textContent = e.message; }
      });
      const save = btn("Save and start the first scan", null, async () => {
        save.disabled = true;
        try {
          const r = await api("/api/setup/save", { method: "POST", body: read() });
          if (!r.ok) { out.className = "err"; out.textContent = r.reason; save.disabled = false; return; }
          wizard(3, {});
        } catch (e) { out.className = "err"; out.textContent = e.message; save.disabled = false; }
      });
      save.disabled = true;
      const row = el("div", "row"); row.append(btn("Back", "ghost", () => wizard(1, pick.ssh ? { ssh: { ...pick.ssh, trusted: true } } : {})), test, save);
      card.append(row, out);
    } else {
      card.append(el("h1", null, "First scan running…"), el("p", "muted", "DBaba is reading the database's own status and catalog. Nothing is changed."));
      const wait = async () => {
        try { const r = await api("/api/report"); if (r.at) return tabs("report"); } catch (e) { /* keep waiting */ }
        setTimeout(wait, 1500);
      };
      setTimeout(wait, 1200);
    }
    app.replaceChildren(card);
  }

  // ---------------------------------------------------------------- the three parts
  async function tabs(which) {
    try { state = await api("/api/state"); } catch (e) { if (e.status === 401) return login(); }
    const nav = el("nav", "tabs");
    const unread = (state.alerts || []).filter((a) => !a.acknowledged && a.level !== "info").length;
    const body = el("div");
    for (const [id, name] of [["chat", "Chat"], ["report", "Report"], ["automation", "Automation"]]) {
      const b = btn(name, id === which ? "on" : null, () => tabs(id));
      if (id === "automation" && unread) b.append(el("span", "badge", String(unread)));
      nav.append(b);
    }
    const db = state.database;
    document.getElementById("who").textContent = db ? `${ENGINES[db.engine] ? ENGINES[db.engine][0] : db.engine} · ${db.name} on ${db.host}:${db.port}${db.ssh ? ` via SSH ${db.ssh.user}@${db.ssh.host}` : ""} · ${db.connected ? "connected" : "not connected"}` : "";
    app.replaceChildren(nav, body);
    if (which === "chat") chat(body);
    else if (which === "report") reportView(body);
    else automation(body);
    clearInterval(poll);
    poll = setInterval(async () => {
      try {
        const s = await api("/api/state");
        const n = (s.alerts || []).filter((a) => !a.acknowledged && a.level !== "info").length;
        const tab = nav.children[2]; tab.querySelector(".badge")?.remove(); if (n) tab.append(el("span", "badge", String(n)));
      } catch (e) { /* the next poll tries again */ }
    }, 60000);
  }

  let draft = "";
  function chat(root) {
    const log = el("div"); const ta = el("textarea"); ta.placeholder = "Ask anything about your database: what is an index, why is this query slow, paste an error…";
    ta.value = draft; draft = "";
    const send = btn("Send", null, async () => {
      const text = ta.value.trim(); if (!text) return;
      log.append(el("div", "msg me", text)); ta.value = ""; send.disabled = true;
      try { const r = await api("/api/message", { method: "POST", body: { text } }); log.append(el("div", "msg bot", r.reply)); }
      catch (e) { log.append(el("div", "msg bot err", e.message)); }
      send.disabled = false; ta.focus();
    });
    ta.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) send.click(); });
    const row = el("div", "row"); row.append(send, btn("New chat", "ghost", async () => { await api("/api/new-chat", { method: "POST", body: {} }); log.replaceChildren(); }));
    const card = el("div", "card"); card.append(log, ta, row); root.append(card); ta.focus();
  }

  async function reportView(root) {
    const card = el("div", "card"); root.append(card);
    let r; try { r = await api("/api/report"); } catch (e) { card.append(el("p", "err", e.message)); return; }
    const head = el("div", "row");
    head.append(btn("Scan now", "ghost", async () => { await api("/api/report/scan", { method: "POST", body: {} }); card.replaceChildren(el("p", "muted", "Scanning…")); setTimeout(() => tabs("report"), 3000); }));
    const dl = el("a", null, "Download as HTML"); dl.href = "/api/report.html"; head.append(dl);
    if (!r.at) { card.append(el("p", "muted", r.pending ? "The scan is running…" : "No scan yet."), head); return; }
    card.append(el("div", "score", `${r.score}/100`), el("p", "muted", `${r.engine} · ${r.at}`), head);
    const ch = r.changes || {};
    if (ch.first === false) {
      const box = el("div", "item info");
      box.append(el("h3", null, `Since the last report (${ch.at_before}): score ${ch.score_before} → ${r.score}`));
      if ((ch.new || []).length) box.append(el("p", null, "New: " + ch.new.join("; ")));
      if ((ch.gone || []).length) box.append(el("p", "good", "Fixed: " + ch.gone.join("; ")));
      if (!(ch.new || []).length && !(ch.gone || []).length) box.append(el("p", "muted", "Nothing changed."));
      card.append(box);
    }
    for (const s of r.sections) {
      card.append(el("h2", null, s.title));
      for (const it of s.items) {
        const box = el("div", `item ${it.severity}`);
        box.append(el("h3", null, `${it.severity.toUpperCase()}: ${it.title}`), el("p", null, it.what), el("p", "muted", `Why it matters: ${it.why}`));
        if (it.fix) box.append(el("pre", null, it.fix.sql), el("p", "muted", "A suggestion only: nothing runs without your approval."));
        box.append(btn("Ask about this", "link", () => { draft = `Explain this finding and how to fix it safely: ${it.title}. ${it.what}`; tabs("chat"); }));
        card.append(box);
      }
    }
  }

  async function automation(root) {
    const card = el("div", "card"); root.append(card);
    card.append(el("h2", null, "Alerts"));
    const alerts = state.alerts || [];
    if (!alerts.length) card.append(el("p", "muted", "No alerts."));
    for (const a of alerts.slice(0, 30)) {
      const box = el("div", `item ${a.level === "info" ? "info" : a.level}`);
      box.append(el("h3", null, `${a.level.toUpperCase()}: ${a.title}`), el("p", null, a.text), el("p", "muted", a.at));
      if (!a.acknowledged) box.append(btn("Mark as seen", "link", async () => { await api(`/api/alerts/${encodeURIComponent(a.id)}/ack`, { method: "POST", body: {} }); tabs("automation"); }));
      card.append(box);
    }
    card.append(el("h2", null, "Jobs"));
    if (state.database && state.database.via && state.database.via !== "here")
      card.append(el("p", "muted", "DBaba runs on this computer, not on the database server: these jobs run only while this computer " +
        "is on and DBaba is open (a missed daily scan runs when it is back). To have them run 24/7, install DBaba on the server."));
    let jobs; try { jobs = (await api("/api/jobs")).jobs; } catch (e) { card.append(el("p", "err", e.message)); return; }
    const table = el("table"); const hr = el("tr");
    for (const h of ["Job", "Runs", "On", "Time", "Last run"]) hr.append(el("th", null, h));
    table.append(hr);
    for (const j of jobs) {
      const tr = el("tr"); const on = el("input"); on.type = "checkbox"; on.checked = j.enabled;
      const at = el("input"); at.type = "time"; at.value = j.at || ""; at.disabled = j.every === "5min";
      const saveJob = async () => { try { await api(`/api/jobs/${encodeURIComponent(j.id)}`, { method: "POST", body: { enabled: on.checked, at: j.every === "5min" ? null : (at.value || j.at) } }); } catch (e) { alert(e.message); } };
      on.addEventListener("change", saveJob); at.addEventListener("change", saveJob);
      const last = j.last_run ? `${j.last_run.ok ? "ok" : "failed"}: ${j.last_run.text} (${j.last_run.at})` : "not yet";
      const c1 = el("td"); c1.append(on); const c2 = el("td"); c2.append(j.every === "5min" ? el("span", "muted", "—") : at);
      tr.append(el("td", null, j.name), el("td", null, j.every === "5min" ? "every 5 minutes" : "every day"), c1, c2, el("td", "muted", last));
      table.append(tr);
    }
    card.append(table);
    await apiKeys(card);
  }

  // API keys for the dbaba command, the desktop app or a script: a new key is shown once, here only
  async function apiKeys(card) {
    card.append(el("h2", null, "API keys"),
      el("p", "muted", "For the dbaba command, the desktop app or your own scripts. A key is shown once; DBaba keeps only a fingerprint of it."));
    const list = el("div"); card.append(list);
    const shown = el("div"); card.append(shown);
    const draw = (keys) => {
      list.replaceChildren();
      if (!keys.length) list.append(el("p", "muted", "No API keys yet."));
      for (const k of keys) {
        const row = el("div", "row");
        row.append(el("span", null, `${k.name} · created ${k.created}`), btn("Revoke", "link", async () => {
          if (!confirm(`Revoke the key "${k.name}"? Anything using it stops working.`)) return;
          try { draw((await api(`/api/keys/${encodeURIComponent(k.id)}/revoke`, { method: "POST", body: {} })).keys); }
          catch (e) { shown.replaceChildren(el("p", "err", e.message)); }
        }));
        list.append(row);
      }
    };
    try { draw((await api("/api/keys")).keys); } catch (e) { list.replaceChildren(el("p", "err", e.message)); return; }
    const name = el("input"); name.placeholder = "Name, e.g. laptop cli"; name.maxLength = 40;
    const make = btn("Create API key", null, async () => {
      try {
        const r = await api("/api/keys", { method: "POST", body: { name: name.value } });
        draw(r.keys); name.value = "";
        const box = el("div", "item warning");
        const code = el("pre", null, r.api_key);
        box.append(el("h3", null, "Copy this key now: it won't be shown again"), code,
          el("p", "muted", "Use it as DBABA_API_KEY for the dbaba command, or paste it into the desktop app."));
        shown.replaceChildren(box);
      } catch (e) { shown.replaceChildren(el("p", "err", e.message)); }
    });
    const row = el("div", "row"); row.append(name, make); card.append(row);
  }

  start();
})();
