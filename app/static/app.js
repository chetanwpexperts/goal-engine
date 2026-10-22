// Goal Engine UI. All user and machine text is inserted with textContent (never innerHTML).
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const icon = (paths) => {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    for (const d of paths) {
      const p = document.createElementNS(ns, "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  };
  const ICONS = {
    when: ["M12 7v5l3 2", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"],
    connect: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
    read: ["M6 3h9l3 3v15H6z", "M9 10h6M9 14h6M9 18h4"],
    think: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-4-4"],
    check: ["M4 12l5 5L20 6"],
    verify: ["M4 12l5 5L20 6"],
    do: ["M4 12l16-8-6 16-2-6z"],
    act: ["M4 12l16-8-6 16-2-6z"],
  };

  const body = document.body;
  const thread = $("#thread");
  const input = $("#input");
  const ghost = $("#ghost");
  const send = $("#send");
  const optionsEl = $("#options");
  const wordcount = $("#wordcount");
  let currentGoalId = null;
  let wordTarget = 0;
  let lastLang = "hi";

  const RUN_WORDS = {
    hi: { preview: "Preview dekho", start: "Start", pause: "Roko", again: "Phir se chalao", checking: "Chal raha hai…",
          paste: "Ya rows paste karo (CSV)", template: "Jo message jayega", none: "Aaj kisi ko message nahi jayega.",
          skipped: "bina email, chhode", cols: "Sheet mein ye columns chahiye", owner: "Start se pehle apna email verify karo. Customers ke replies isi pe aayenge.",
          ownerEmail: "aapka@email.com", business: "Business ka naam", sendCode: "Code bhejo", code: "6-digit code", verify: "Verify",
          verified: "Verified", next: "Agla run", noAccess: "Koi password ya access nahi chahiye", connector: "Aapke server pe read-only connector chahiye",
          soon: "Ye agent abhi ban raha hai. Aapki zaroorat note kar li hai.", copy: "Copy", copied: "Copied",
          rewritten: "Rewritten query", indexes: "Index commands", mapping: "Columns samjhe",
          state: { proposed: "Taiyar", planned: "Jald aa raha", running: "Chal raha", done: "Ho gaya", failed: "Ruk gaya", active: "Chal raha hai", paused: "Ruka hua" } },
    en: { preview: "Preview", start: "Start", pause: "Pause", again: "Run again", checking: "Working…",
          paste: "Or paste the rows (CSV)", template: "The message", none: "Nobody would get a message today.",
          skipped: "without email, skipped", cols: "Your sheet needs these columns", owner: "Verify your email before Start. Customer replies come here.",
          ownerEmail: "you@business.com", business: "Business name", sendCode: "Send code", code: "6-digit code", verify: "Verify",
          verified: "Verified", next: "Next run", noAccess: "No password or access needed", connector: "Needs a read-only connector on your server",
          soon: "This agent is being built. Your need is noted.", copy: "Copy", copied: "Copied",
          rewritten: "Rewritten query", indexes: "Index commands", mapping: "Columns understood",
          state: { proposed: "Ready", planned: "Coming soon", running: "Running", done: "Done", failed: "Stopped", active: "Running", paused: "Paused" } },
  };
  const W = (lang) => RUN_WORDS[lang === "en" ? "en" : "hi"];

  async function api(path, options = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...options });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) { const e = new Error(data.error || data.detail || "Something went wrong. Try again."); e.data = data; throw e; }
    return data;
  }

  // ---------------------------------------------------------------- composer
  const EXAMPLES = [
    "Jinka payment baaki hai unko har Monday reminder chala jaye",
    "Meri website slow hai, check karo",
    "Patients ko appointment se ek din pehle yaad dilao",
    "Roz subah 9 baje pending payments ki report chahiye",
    "Database se orders ka data Excel mein chahiye",
    "Jo customer 30 din se nahi aaye unko message bhejo",
  ];
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let typer = null;

  function typeExamples() {
    clearTimeout(typer);
    if (body.dataset.state !== "idle") { ghost.textContent = input.value ? "" : (lastLang === "en" ? "Anything else? Type or speak" : "Aur kuch? Likho ya bolo"); return; }
    if (reduceMotion) { ghost.textContent = input.value ? "" : EXAMPLES[0]; return; }
    let i = 0, n = 0, deleting = false;
    const step = () => {
      if (input.value || body.dataset.state !== "idle") { ghost.textContent = ""; typer = setTimeout(step, 600); return; }
      const text = EXAMPLES[i];
      n += deleting ? -2 : 1;
      ghost.textContent = text.slice(0, Math.max(0, n));
      let wait = deleting ? 18 : 45;
      if (!deleting && n >= text.length) { deleting = true; wait = 2200; }
      if (deleting && n <= 0) { deleting = false; i = (i + 1) % EXAMPLES.length; wait = 350; }
      typer = setTimeout(step, wait);
    };
    step();
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${input.scrollHeight}px`;
    ghost.hidden = Boolean(input.value);
    updateWordcount();
  }

  function updateWordcount() {
    if (!wordTarget) { wordcount.hidden = true; return; }
    const n = (input.value.trim().match(/\S+/g) || []).length;
    wordcount.hidden = false;
    wordcount.textContent = n >= wordTarget ? `${n} words` : `${n} / ${wordTarget} words`;
    wordcount.classList.toggle("ok", n >= wordTarget);
  }

  function setActive() {
    if (body.dataset.state === "active") return;
    body.dataset.state = "active";
    clearTimeout(typer);
    typeExamples();
  }

  function showOptions(options) {
    optionsEl.replaceChildren();
    optionsEl.hidden = !(options && options.length);
    for (const label of options || []) {
      const b = el("button", null, label);
      b.type = "button";
      b.addEventListener("click", () => sendMessage(label));
      optionsEl.append(b);
    }
  }

  // ---------------------------------------------------------------- thread
  function addUser(text) {
    setActive();
    const p = el("p", "turn-user", text);
    thread.append(p);
    p.scrollIntoView({ block: "end", behavior: reduceMotion ? "auto" : "smooth" });
  }

  function addMachine(text, trace = [], error = false) {
    const box = el("div", `turn-machine${error ? " error" : ""}`);
    if (trace.length) {
      const ul = el("ul", "trace");
      trace.forEach((line, i) => {
        const li = el("li", null, line);
        li.style.animationDelay = `${i * 110}ms`;
        ul.append(li);
      });
      box.append(ul);
    }
    box.append(el("p", "say", text));
    thread.append(box);
    box.scrollIntoView({ block: "end", behavior: reduceMotion ? "auto" : "smooth" });
    return box;
  }

  function thinking() {
    const box = el("div", "turn-machine");
    const ul = el("ul", "trace");
    ul.append(el("li", "live", lastLang === "en" ? "Understanding…" : "Samajh raha hoon…"));
    box.append(ul);
    thread.append(box);
    return box;
  }

  function traceFor(data) {
    const hi = (data.goal ? data.goal.detected_language !== "English" : lastLang !== "en");
    const t = [];
    if (data.goal) {
      t.push(hi ? `${data.goal.detected_language || "Hinglish"} samjha` : `Understood (${data.goal.detected_language || "English"})`);
      if (["new_goal", "update_goal", "corrected", "agent_plan"].includes(data.relation)) t.push(`${hi ? "Goal" : "Goal"}: ${data.goal.title}`);
    }
    const agent = (data.agents || [])[0];
    if (agent && data.relation === "agent_plan") t.push(hi ? `${agent.blocks.length} blocks se agent banaya` : `Built an agent from ${agent.blocks.length} blocks`);
    if (data.relation === "agent_run") t.push(hi ? "Agent ne kaam kiya" : "Agent did the work");
    if (data.relation === "howto") t.push(hi ? "Step samjhaya" : "Explained the step");
    return t;
  }

  // ---------------------------------------------------------------- what the machine understood
  function renderUnderstood(goal) {
    const box = $("#understood");
    box.replaceChildren();
    if (!goal) { box.hidden = true; return; }
    box.hidden = false;
    box.append(el("span", "fact goal", goal.title));
    for (const f of goal.known_facts || []) box.append(el("span", "fact", f));
    for (const m of goal.missing_info || []) box.append(el("span", "fact missing", m.question));
  }

  // ---------------------------------------------------------------- agents
  function stateBadge(a, w) {
    return el("span", `state ${a.status}`, w.state[a.status] || a.status);
  }

  function blockLine(blocks) {
    const ol = el("ol", "line");
    blocks.forEach((b, i) => {
      const li = el("li");
      li.style.animationDelay = `${i * 90}ms`;
      const node = el("span", "node");
      node.append(icon(ICONS[b.kind] || ICONS.think));
      li.append(node, el("p", null, b.text));
      ol.append(li);
    });
    return ol;
  }

  function codeBox(caption, code, w) {
    const box = el("div", "code");
    if (caption) box.append(el("p", "cap", caption));
    const pre = el("pre", null, code);
    const copy = el("button", null, w.copy);
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(code); copy.textContent = w.copied; } catch (_) { /* no clipboard */ }
    });
    box.append(pre, copy);
    return box;
  }

  function checkResult(run, w, hi) {
    const wrap = el("div", "result");
    const r = run.result || {};
    if (typeof r.score === "number") {
      const s = el("p", "score");
      s.append(el("b", null, String(r.score)), el("span", null, `/ 100 · ${r.url || ""}`));
      wrap.append(s);
    }
    wrap.append(el("p", "result-head", run.summary || ""));
    if (r.findings && r.findings.length) {
      const ul = el("ul", "found");
      for (const f of r.findings) {
        const li = el("li", `s${f.severity}`);
        li.append(el("div", "t", hi ? f.title_hi : f.title_en));
        const fix = hi ? f.fix_hi : f.fix_en;
        if (fix) li.append(el("div", "f", fix));
        if (f.evidence) li.append(el("div", "e", f.evidence));
        ul.append(li);
      }
      wrap.append(ul);
    }
    if (r.rewritten) wrap.append(codeBox(w.rewritten, r.rewritten, w));
    if (r.indexes && r.indexes.length) wrap.append(codeBox(w.indexes, r.indexes.join("\n"), w));
    if (r.note) wrap.append(el("p", "note", r.note));
    return wrap;
  }

  function automationResult(run, w) {
    const wrap = el("div", "result");
    const r = run.result || {};
    wrap.append(el("p", "result-head", run.summary || ""));
    if (r.mapping && Object.keys(r.mapping).length) {
      wrap.append(el("p", "mapping", `${w.mapping}: ` + Object.entries(r.mapping).map(([k, v]) => `${k} = ${v}`).join(", ")));
    }
    if (r.report) wrap.append(codeBox(r.report.subject, r.report.body, w));
    if (r.messages) {
      if (!r.messages.length) wrap.append(el("p", "note", w.none));
      const list = el("div", "found");
      for (const m of r.messages.slice(0, 8)) {
        const card = el("div", "mail");
        card.append(el("div", "to", m.to), el("div", "sub", m.subject), el("pre", null, m.body));
        list.append(card);
      }
      wrap.append(list);
      if (r.messages.length > 8) wrap.append(el("p", "note", `+${r.messages.length - 8}`));
    }
    if (r.skipped && r.skipped.length) wrap.append(el("p", "note", `${r.skipped.length} ${w.skipped}`));
    return wrap;
  }

  function ownerBox(a, w) {
    const box = el("div", "owner");
    if (a.owner.verified) {
      box.classList.add("done");
      box.append(el("p", null, `${w.verified}: ${a.owner.email}`));
      return box;
    }
    box.append(el("p", null, w.owner));
    const row1 = el("div", "row");
    const email = el("input"); email.type = "email"; email.placeholder = w.ownerEmail; email.value = a.owner.email || "";
    const biz = el("input"); biz.type = "text"; biz.placeholder = w.business; biz.value = a.owner.business || "";
    row1.append(email, biz);
    const row2 = el("div", "row");
    const sendCode = el("button", "btn solid", w.sendCode); sendCode.type = "button";
    const code = el("input"); code.type = "text"; code.inputMode = "numeric"; code.maxLength = 6; code.placeholder = w.code; code.hidden = true;
    const verify = el("button", "btn solid", w.verify); verify.type = "button"; verify.hidden = true;
    row2.append(sendCode, code, verify);
    sendCode.addEventListener("click", async () => {
      sendCode.disabled = true;
      try {
        await api("/api/owner/code", { method: "POST", body: JSON.stringify({ email: email.value, business: biz.value }) });
        code.hidden = false; verify.hidden = false; code.focus();
      } catch (err) { addMachine(err.message, [], true); }
      sendCode.disabled = false;
    });
    verify.addEventListener("click", async () => {
      try {
        await api("/api/owner/verify", { method: "POST", body: JSON.stringify({ code: code.value }) });
        await refreshGoal();
      } catch (err) { addMachine(err.message, [], true); }
    });
    box.append(row1, row2);
    return box;
  }

  function renderAutomation(a) {
    const w = W(a.lang);
    const card = el("article", `agent${a.status === "active" ? " is-active" : ""}`);
    const head = el("div", "agent-name");
    head.append(el("h2", null, a.name), stateBadge(a, w));
    card.append(head, blockLine(a.blocks));
    const run = a.runs && a.runs[0];
    const previewOk = run && run.status === "done";

    if (a.status === "active") {
      if (a.next_run_at) {
        const when = new Date(`${a.next_run_at}Z`).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
        card.append(el("p", "note", `${w.next}: ${when}`));
      }
      const acts = el("div", "acts");
      const pause = el("button", "btn", w.pause);
      pause.type = "button";
      pause.addEventListener("click", async () => renderAgents((await api(`/api/agents/${a.id}/pause`, { method: "POST" })).agents));
      acts.append(pause);
      card.append(acts);
    } else {
      const slot = el("div", "slot");
      if (a.needs_columns && a.needs_columns.length) slot.append(el("label", null, `${w.cols}: ${a.needs_columns.join(", ")}`));
      const url = el("input");
      url.type = "url";
      url.placeholder = "https://docs.google.com/spreadsheets/d/…";
      url.setAttribute("aria-label", a.inputs[0].label);
      url.value = a.inputs[0].value || "";
      const more = el("details");
      const csv = el("textarea");
      csv.value = a.inputs[1].value || "";
      csv.placeholder = "Name,Email,Amount,Due Date\nRavi,ravi@example.com,2500,25/10/2026";
      more.append(el("summary", null, w.paste), csv);
      if (csv.value) more.open = true;
      slot.append(url, more);
      if (a.message) {
        const tpl = el("details");
        tpl.append(el("summary", null, w.template), el("div", "msg-template", a.message));
        slot.append(tpl);
      }
      card.append(slot);
      const acts = el("div", "acts");
      const pv = el("button", previewOk ? "btn" : "btn solid", w.preview);
      pv.type = "button";
      pv.addEventListener("click", async () => {
        pv.disabled = true; pv.textContent = w.checking;
        try {
          const data = await api(`/api/agents/${a.id}/preview`, { method: "POST", body: JSON.stringify({ inputs: { sheet_url: url.value, csv_text: csv.value } }) });
          addMachine(data.reply, [a.lang === "en" ? "Read the sheet" : "Sheet padhi", a.lang === "en" ? "Picked the right rows" : "Sahi log chune"]);
          renderAgents(data.agents);
        } catch (err) { addMachine(err.message, [], true); pv.disabled = false; pv.textContent = w.preview; }
      });
      acts.append(pv);
      if (previewOk) {
        const go = el("button", "btn go", w.start);
        go.type = "button";
        go.disabled = a.needs_owner && !a.owner.verified;
        go.addEventListener("click", async () => {
          go.disabled = true;
          try {
            const data = await api(`/api/agents/${a.id}/activate`, { method: "POST" });
            addMachine(data.reply, [a.lang === "en" ? "Agent switched on" : "Agent chalu"]);
            renderAgents(data.agents);
            refreshCount();
          } catch (err) { addMachine(err.message, [], true); go.disabled = false; }
        });
        acts.append(go);
      }
      card.append(acts);
      if (a.needs_owner && previewOk) card.append(ownerBox(a, w));
    }
    if (run) card.append(automationResult(run, w));
    return card;
  }

  function renderCheckAgent(a) {
    const w = W(a.lang);
    const hi = a.lang !== "en";
    const card = el("article", `agent${a.status === "active" ? " is-active" : ""}`);
    const head = el("div", "agent-name");
    head.append(el("h2", null, a.name), stateBadge(a, w));
    card.append(head, blockLine(a.blocks));
    card.append(el("p", `access ${a.access}`, a.access === "none" ? w.noAccess : w.connector));
    if (!a.available) { card.append(el("p", "note", w.soon)); return card; }
    const run = a.runs && a.runs[0];
    const fields = {};
    if (a.status !== "active" && !run) {
      const slot = el("div", "slot");
      for (const inp of a.inputs) {
        const f = inp.multiline ? el("textarea") : el("input");
        if (!inp.multiline) f.type = "text";
        f.value = inp.value || "";
        f.placeholder = inp.kind === "url" ? "example.com" : inp.label;
        f.setAttribute("aria-label", inp.label);
        fields[inp.key] = f;
        slot.append(el("label", null, inp.label), f);
      }
      card.append(slot);
    }
    const acts = el("div", "acts");
    if (a.status === "active") {
      const pause = el("button", "btn", w.pause);
      pause.type = "button";
      pause.addEventListener("click", async () => renderAgents((await api(`/api/agents/${a.id}/pause`, { method: "POST" })).agents));
      acts.append(pause);
    } else {
      const go = el("button", run ? "btn" : "btn solid", run ? w.again : w.start);
      go.type = "button";
      go.addEventListener("click", async () => {
        const inputs = {};
        for (const [k, f] of Object.entries(fields)) inputs[k] = f.value;
        go.disabled = true; go.textContent = w.checking;
        try {
          const data = await api(`/api/agents/${a.id}/start`, { method: "POST", body: JSON.stringify({ inputs }) });
          addMachine(data.reply, [hi ? "Agent ne kaam kiya" : "Agent did the work"]);
          renderAgents(data.agents);
          showOptions(data.options || []);
        } catch (err) { addMachine(err.message, [], true); go.disabled = false; go.textContent = run ? w.again : w.start; }
      });
      acts.append(go);
    }
    card.append(acts);
    if (run) card.append(checkResult(run, w, hi));
    return card;
  }

  // ---------------------------------------------------------------- database agent (through the connector)
  const T = (hi, h, e) => (hi ? h : e);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let connectorWatch = null;

  function fmtBytes(n) {
    if (!n) return "";
    const u = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
  }

  async function runJob(cid, body, onWait) {
    const job = await api(`/api/connectors/${cid}/jobs`, { method: "POST", body: JSON.stringify(body) });
    for (let i = 0; i < 90; i++) {
      await sleep(i < 4 ? 800 : 1500);
      const j = await api(`/api/connector-jobs/${job.id}`);
      if (j.status === "done") return j;
      if (j.status === "failed" || j.status === "expired") throw new Error(j.error || "The job didn't finish");
      if (onWait && i === 6) onWait();
    }
    throw new Error("The connector isn't answering. Is it running on your server?");
  }

  function grid(result) {
    const wrap = el("div", "grid");
    const table = el("table");
    const tr = el("tr");
    for (const c of result.columns || []) tr.append(el("th", null, c));
    const thead = el("thead"); thead.append(tr); table.append(thead);
    const tb = el("tbody");
    for (const row of result.rows || []) {
      const r = el("tr");
      for (const v of row) r.append(el("td", v === null ? "nil" : null, v === null ? "NULL" : String(v)));
      tb.append(r);
    }
    table.append(tb); wrap.append(table);
    return wrap;
  }

  function healthView(r, hi) {
    const wrap = el("div", "result");
    wrap.append(el("p", "result-head", T(hi, `Database ${r.version}`, `Database ${r.version}`)));
    const ul = el("ul", "found");
    for (const c of r.checks || []) {
      const li = el("li", c.level === "warn" ? "s2" : c.level === "ok" ? "s0" : "s1");
      li.append(el("div", "t", c.text));
      ul.append(li);
    }
    wrap.append(ul);
    if (r.biggest && r.biggest.length) {
      wrap.append(el("p", "cap", T(hi, "Sabse badi tables", "Biggest tables")));
      wrap.append(grid({ columns: [T(hi, "Table", "Table"), T(hi, "Rows", "Rows"), T(hi, "Size", "Size")],
                         rows: r.biggest.map((t) => [t.name, t.rows, fmtBytes(t.bytes)]) }));
    }
    return wrap;
  }

  function watchConnector(cid, agentId) {
    clearInterval(connectorWatch);
    let n = 0;
    connectorWatch = setInterval(async () => {
      if (++n > 200) { clearInterval(connectorWatch); return; }
      try {
        const { connectors } = await api("/api/connectors");
        const c = connectors.find((x) => x.id === cid);
        if (c && c.online && c.tables.length) { clearInterval(connectorWatch); await refreshGoal(); }
      } catch (_) { /* keep waiting */ }
    }, 3000);
  }

  function setupBox(a, hi) {
    const box = el("div", "slot");
    if (a.connectors && a.connectors.length) {
      box.append(el("label", null, T(hi, "Pehle se juda database use karo", "Use a database you already connected")));
      const row = el("div", "acts flat");
      for (const c of a.connectors) {
        const b = el("button", "btn", `${c.name}${c.db_name && c.db_name !== c.name ? ` · ${c.db_name}` : ""}`);
        b.type = "button";
        b.addEventListener("click", async () => renderAgents((await api(`/api/agents/${a.id}/connect/${c.id}`, { method: "POST" })).agents));
        row.append(b);
      }
      box.append(row, el("label", null, T(hi, "Ya naya database jodo", "Or connect a new one")));
    }

    // Typed on Goal Engine: no terminal
    const vps = el("details", "vps");
    const form = el("form", "dbform");
    const field = (label, input) => { const l = el("label", "f"); l.append(el("span", null, label), input); return l; };
    const type = el("select");
    for (const [v, t] of [["mysql", "MySQL / MariaDB"], ["postgres", "PostgreSQL"]]) { const o = el("option", null, t); o.value = v; type.append(o); }
    const host = el("input"); host.placeholder = T(hi, "jaise db.mysite.com ya server ka IP", "e.g. db.mysite.com or the server IP"); host.autocomplete = "off"; host.required = true;
    const port = el("input"); port.inputMode = "numeric"; port.placeholder = "3306"; port.maxLength = 5;
    type.addEventListener("change", () => { port.placeholder = type.value === "postgres" ? "5432" : "3306"; });
    const dbn = el("input"); dbn.autocomplete = "off"; dbn.required = true;
    const user = el("input"); user.autocomplete = "off"; user.required = true;
    const pass = el("input"); pass.type = "password"; pass.autocomplete = "new-password";
    const r1 = el("div", "row two"); r1.append(field(T(hi, "Database", "Database"), type), field("Port", port));
    const r2 = el("div", "row two"); r2.append(field(T(hi, "Database ka naam", "Database name"), dbn), field(T(hi, "User", "User"), user));
    form.append(r1, field("Host", host), r2, field(T(hi, "Password", "Password"), pass));
    const hint = el("p", "fine", T(hi, "Sirf padhne wala (read-only) user sabse safe hai. cPanel mein: MySQL Databases → naya user, sirf SELECT. Password encrypt karke rakha jaata hai, hum data badal nahi sakte.",
                                     "A read-only user is safest. In cPanel: MySQL Databases → new user with SELECT only. The password is stored encrypted, and nothing can change your data."));
    const ipHint = el("p", "fine");
    api("/api/connector-info").then((d) => {
      if (d.server_ip) ipHint.textContent = T(hi, `Remote access mein ye IP allow karo: ${d.server_ip}`, `Allow this IP for remote access: ${d.server_ip}`);
    }).catch(() => {});
    const go = el("button", "btn solid", T(hi, "Jodo", "Connect")); go.type = "submit";
    const err = el("p", "note err");
    err.hidden = true;
    const ga = el("div", "acts flat"); ga.append(go);
    form.append(hint, ipHint, ga, err);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      go.disabled = true; go.textContent = T(hi, "Jod raha hoon…", "Connecting…"); err.hidden = true;
      try {
        const data = await api("/api/connectors/direct", { method: "POST", body: JSON.stringify({
          type: type.value, host: host.value, port: port.value, database: dbn.value, user: user.value, password: pass.value, agent_id: a.id }) });
        pass.value = "";
        addMachine(T(hi, "Database jud gaya. Tables padh raha hoon.", "Database connected. Reading the tables."), [T(hi, "Read-only connection bana", "Read-only connection made")]);
        renderAgents(data.agents);
      } catch (ex) {
        err.textContent = ex.message; err.hidden = false;
        go.disabled = false; go.textContent = T(hi, "Jodo", "Connect");
        if (ex.data && ex.data.use_command) { vps.open = true; vps.classList.add("hint"); vps.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" }); }
      }
    });
    box.append(form);

    // On a VPS where the database only listens locally: one command on the server
    vps.append(el("summary", null, T(hi, "Database sirf server ke andar chalta hai (VPS)? Ek command se jodo", "Database only reachable inside your server (VPS)? Connect with one command")));
    const make = el("button", "btn", T(hi, "Command banao", "Get the command")); make.type = "button";
    const inner = el("div", "slot inner"); inner.append(make);
    vps.append(inner);
    make.addEventListener("click", async () => {
      make.disabled = true;
      try {
        const data = await api("/api/connectors", { method: "POST", body: JSON.stringify({ agent_id: a.id }) });
        const w = W(a.lang);
        inner.replaceChildren(
          el("label", null, T(hi, "Ye ek command server ke terminal mein paste karo. Ye sirf ek baar dikhega:",
                                  "Paste this one command in your server's terminal. It's shown only once:")),
          codeBox("", data.command, w),
          el("p", "fine", T(hi, "Ye aapke project ki settings khud dhoondh lega, aap bas Enter dabao. Password aapke server pe hi rehta hai.",
                                "It finds your project's database settings by itself; you just press Enter. The password stays on your server.")),
          el("p", "waiting", T(hi, "Connector ka intezaar…", "Waiting for the connector…")));
        watchConnector(data.connector.id, a.id);
      } catch (ex) { addMachine(ex.message, [], true); make.disabled = false; }
    });
    box.append(vps);
    return box;
  }

  function renderDatabase(a) {
    const w = W(a.lang);
    const hi = a.lang !== "en";
    const card = el("article", "agent db");
    const head = el("div", "agent-name");
    head.append(el("h2", null, a.name));
    const c = a.connector;
    const st = el("span", `state ${c && c.online ? "active" : c ? "paused" : "proposed"}`,
                  c && c.online ? T(hi, "Juda hua", "Connected") : c ? T(hi, "Offline", "Offline") : T(hi, "Taiyar", "Ready"));
    head.append(st);
    card.append(head, blockLine(a.blocks));
    if (!a.available) card.append(el("p", "note", T(hi, "Ye level abhi ban raha hai. Tab tak data dekh, export aur report kar sakte ho.",
                                                      "This level is being built. Until then you can view, export and report.")));
    if (!c) { card.append(setupBox(a, hi)); return card; }

    const out = el("div", "db-out");
    const say = (text) => { out.replaceChildren(el("p", "waiting", text)); };
    const fail = (err) => { out.replaceChildren(el("p", "note err", err.message)); };
    const meta = el("p", "access connector", `${c.name}${c.db_name && c.db_name !== c.name ? ` · ${c.db_name}` : ""}${c.db_type ? ` (${c.db_type})` : ""} · ${T(hi, "sirf read-only", "read-only")}`);
    card.append(meta);
    if (c.can_write) card.append(el("p", "note warn", T(hi, "Is database user ke paas data badalne ka haq hai. Goal Engine kabhi kuch badalta nahi, phir bhi sirf-SELECT wala user behtar hai.",
                                                       "This database user can change data. Goal Engine never changes anything, but a SELECT-only user is safer.")));
    if (!c.online && c.kind !== "direct") {
      card.append(el("p", "note", T(hi, "Connector band hai. Server pe chalao: cd ~/goal-connector && .venv/bin/python goal_connector.py run &", "The connector isn't running. On your server: cd ~/goal-connector && .venv/bin/python goal_connector.py run &")));
      watchConnector(c.id, a.id);
    }

    const tables = el("div", "db-tables");
    if (c.tables.length) {
      const filter = el("input"); filter.type = "search"; filter.placeholder = T(hi, `${c.tables.length} tables, naam se dhoondo`, `${c.tables.length} tables, search by name`);
      const list = el("ul");
      const draw = () => {
        list.replaceChildren();
        const q = filter.value.toLowerCase();
        for (const t of c.tables.filter((x) => x.name.toLowerCase().includes(q)).slice(0, 60)) {
          const li = el("li");
          const nm = el("button", "tname", t.name); nm.type = "button";
          const info = el("span", "tinfo", `${t.rows.toLocaleString("en-IN")} ${T(hi, "rows", "rows")}${t.bytes ? ` · ${fmtBytes(t.bytes)}` : ""}`);
          const dl = el("button", "tdl", T(hi, "Excel", "Excel")); dl.type = "button";
          dl.setAttribute("aria-label", `${T(hi, "Download", "Download")} ${t.name}`);
          nm.addEventListener("click", async () => {
            say(T(hi, `${t.name} padh raha hoon…`, `Reading ${t.name}…`));
            try {
              const j = await runJob(c.id, { kind: "query", table: t.name });
              out.replaceChildren(el("p", "cap", T(hi, `${t.name}: pehli ${j.result.row_count} rows`, `${t.name}: first ${j.result.row_count} rows`)), grid(j.result));
            } catch (err) { fail(err); }
          });
          dl.addEventListener("click", async () => {
            say(T(hi, `${t.name} ka poora data nikaal raha hoon…`, `Exporting all of ${t.name}…`));
            try {
              const j = await runJob(c.id, { kind: "export", table: t.name });
              out.replaceChildren(el("p", "cap", T(hi, `${j.result.row_count.toLocaleString("en-IN")} rows taiyar`, `${j.result.row_count.toLocaleString("en-IN")} rows ready`)));
              window.location.href = `/api/connector-jobs/${j.id}/download`;
            } catch (err) { fail(err); }
          });
          li.append(nm, info, dl);
          list.append(li);
        }
      };
      filter.addEventListener("input", draw);
      draw();
      tables.append(filter, list);
    } else if (c.online) {
      tables.append(el("p", "waiting", T(hi, "Tables padh raha hoon…", "Reading the tables…")));
      watchConnector(c.id, a.id);
    }
    card.append(tables);

    // Custom read-only SQL, EXPLAIN and health
    const sql = el("details", "db-sql");
    sql.append(el("summary", null, T(hi, "Apni SQL query chalao (read-only)", "Run your own SQL (read-only)")));
    const ta = el("textarea"); ta.placeholder = "SELECT status, COUNT(*) FROM orders GROUP BY status";
    const row = el("div", "acts flat");
    const mk = (label, kind, solid) => {
      const b = el("button", solid ? "btn solid" : "btn", label); b.type = "button";
      b.addEventListener("click", async () => {
        say(T(hi, "Chal raha hai…", "Running…"));
        try {
          const j = await runJob(c.id, { kind, sql: ta.value });
          if (kind === "export") { out.replaceChildren(el("p", "cap", `${j.result.row_count} rows`)); window.location.href = `/api/connector-jobs/${j.id}/download`; }
          else out.replaceChildren(el("p", "cap", kind === "explain" ? "EXPLAIN" : `${j.result.row_count} rows${j.result.limited ? T(hi, " (pehli 50)", " (first 50)") : ""}`), grid(j.result));
        } catch (err) { fail(err); }
      });
      return b;
    };
    row.append(mk(T(hi, "Chalao", "Run"), "query", true), mk("Excel", "export"), mk("EXPLAIN", "explain"));
    sql.append(ta, row);
    if (a.mode === "explore" || a.mode === "export") card.append(sql);

    const acts = el("div", "acts");
    const hb = el("button", a.mode === "health" ? "btn solid" : "btn", T(hi, "Health check", "Health check")); hb.type = "button";
    hb.addEventListener("click", async () => {
      say(T(hi, "Database check kar raha hoon…", "Checking the database…"));
      try { out.replaceChildren(healthView((await runJob(c.id, { kind: "health" })).result, hi)); } catch (err) { fail(err); }
    });
    acts.append(hb);
    if (a.mode !== "explore" && a.mode !== "export") acts.append(el("span"));
    card.append(acts);
    if (a.mode === "health" || a.mode === "report") card.append(sql);

    if (a.mode === "report" && c.tables.length) {
      const rep = el("div", "slot");
      rep.append(el("label", null, T(hi, "Report kis cheez ki? Table chuno ya upar SQL likho", "Report on what? Pick a table, or write SQL above")));
      const sel = el("select");
      sel.append(el("option", null, T(hi, "— SQL wali query —", "— the SQL query above —")));
      sel.options[0].value = "";
      for (const t of c.tables) { const o = el("option", null, t.name); o.value = t.name; sel.append(o); }
      const every = el("select");
      for (const [v, h, e] of [["day", "Roz", "Every day"], ["week", "Har hafte (Somvar)", "Every week (Monday)"], ["month", "Har mahine (1 tarikh)", "Every month (1st)"]]) {
        const o = el("option", null, T(hi, h, e)); o.value = v; every.append(o);
      }
      const at = el("input"); at.type = "time"; at.value = "09:00";
      const r2 = el("div", "row"); r2.append(sel, every, at);
      const go = el("button", "btn go", T(hi, "Report chalu karo", "Start the report")); go.type = "button";
      go.disabled = !a.owner.verified;
      go.addEventListener("click", async () => {
        go.disabled = true;
        try {
          const data = await api(`/api/agents/${a.id}/db-report`, { method: "POST", body: JSON.stringify({ table: sel.value, sql: sel.value ? "" : ta.value, every: every.value, at: at.value }) });
          addMachine(data.reply, [T(hi, "Report agent chalu", "Report agent switched on")]);
          renderAgents(data.agents); refreshCount();
        } catch (err) { addMachine(err.message, [], true); go.disabled = false; }
      });
      rep.append(r2);
      card.append(rep);
      const ga = el("div", "acts"); ga.append(go); card.append(ga);
      card.append(ownerBox(a, w));
    }
    card.append(out);

    const off = el("button", "linkish", T(hi, "Is database ko disconnect karo", "Disconnect this database")); off.type = "button";
    off.addEventListener("click", async () => {
      await api(`/api/connectors/${c.id}/disconnect`, { method: "POST" });
      await refreshGoal();
    });
    card.append(off);
    return card;
  }

  function renderDbReport(a) {
    const w = W(a.lang);
    const card = el("article", `agent${a.status === "active" ? " is-active" : ""}`);
    const head = el("div", "agent-name");
    head.append(el("h2", null, a.name), stateBadge(a, w));
    card.append(head);
    card.append(el("p", "access connector", a.schedule));
    if (a.next_run_at) {
      const when = new Date(`${a.next_run_at}Z`).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      card.append(el("p", "note", `${w.next}: ${when}`));
    }
    if (a.status === "active") {
      const acts = el("div", "acts");
      const pause = el("button", "btn", w.pause); pause.type = "button";
      pause.addEventListener("click", async () => renderAgents((await api(`/api/agents/${a.id}/pause`, { method: "POST" })).agents));
      acts.append(pause); card.append(acts);
    }
    return card;
  }

  function renderAgents(agents) {
    const box = $("#agents");
    box.replaceChildren();
    for (const a of agents || []) {
      box.append(a.automation ? renderAutomation(a) : a.database ? renderDatabase(a) : a.db_report ? renderDbReport(a) : renderCheckAgent(a));
    }
    if (agents && agents[0]) lastLang = agents[0].lang;
  }

  async function refreshGoal() {
    if (!currentGoalId) return;
    const data = await api(`/api/goals/${currentGoalId}`);
    renderUnderstood(data.goal);
    renderAgents(data.agents || []);
  }

  async function refreshCount() {
    try {
      const data = await api("/api/agents");
      const live = data.agents.filter((a) => a.status === "active").length;
      const badge = $("#agent-count");
      badge.hidden = !live;
      badge.textContent = String(live);
    } catch (_) { /* fine */ }
  }

  // ---------------------------------------------------------------- sending
  async function sendMessage(text) {
    text = text.trim();
    if (!text) return;
    addUser(text);
    input.value = "";
    wordTarget = 0;
    autosize();
    showOptions([]);
    stopListening();
    send.disabled = true;
    const wait = thinking();
    try {
      const data = await api("/api/message", { method: "POST", body: JSON.stringify({ text, goal_id: currentGoalId }) });
      wait.remove();
      if (data.goal) {
        currentGoalId = data.goal.id;
        lastLang = data.goal.detected_language === "English" ? "en" : "hi";
        renderUnderstood(data.goal);
        renderAgents(data.agents || []);
      }
      addMachine(data.reply, traceFor(data));
      showOptions(data.options || []);
      wordTarget = data.ask_words || 0;
      updateWordcount();
    } catch (err) {
      wait.remove();
      addMachine(err.message, [], true);
    } finally {
      send.disabled = false;
      typeExamples();
      input.focus();
    }
  }

  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendMessage(input.value); });
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(input.value); }
  });

  // ---------------------------------------------------------------- voice (the browser's own, free)
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $("#mic");
  const micLang = $("#mic-lang");
  let recognition = null;
  let listening = false;
  let voiceLang = "hi-IN";
  try { voiceLang = localStorage.getItem("ge_voice_lang") || "hi-IN"; } catch (_) { /* storage blocked */ }
  function stopListening() { if (recognition && listening) recognition.stop(); }
  function paintMicLang() { micLang.textContent = voiceLang === "hi-IN" ? "हिंदी" : "English"; }
  if (Recognition) {
    mic.hidden = false;
    micLang.hidden = false;
    paintMicLang();
    micLang.addEventListener("click", () => {
      voiceLang = voiceLang === "hi-IN" ? "en-IN" : "hi-IN";
      try { localStorage.setItem("ge_voice_lang", voiceLang); } catch (_) { /* ignore */ }
      paintMicLang();
    });
    mic.addEventListener("click", () => {
      if (listening) { stopListening(); return; }
      recognition = new Recognition();
      recognition.lang = voiceLang;
      recognition.interimResults = true;
      recognition.continuous = true;
      const base = input.value ? `${input.value.trim()} ` : "";
      recognition.onresult = (event) => {
        let spoken = "";
        for (const r of event.results) spoken += r[0].transcript;
        input.value = base + spoken;
        autosize();
      };
      recognition.onstart = () => { listening = true; mic.classList.add("listening"); };
      recognition.onend = () => { listening = false; mic.classList.remove("listening"); input.focus(); };
      recognition.onerror = (e) => {
        if (e.error === "not-allowed") addMachine("Microphone is blocked. Allow it in the browser to speak.", [], true);
      };
      recognition.start();
    });
  }

  // ---------------------------------------------------------------- drawers
  const drawer = $("#drawer");
  const drawerBody = $("#drawer-body");
  function openDrawer(nodes) {
    drawerBody.replaceChildren(...nodes);
    drawer.hidden = false;
    drawer.querySelector("[data-close]").focus();
  }
  drawer.addEventListener("click", (e) => { if (e.target === drawer || e.target.closest("[data-close]")) drawer.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") drawer.hidden = true; });

  async function openGoal(id) {
    drawer.hidden = true;
    const data = await api(`/api/goals/${id}`);
    currentGoalId = id;
    setActive();
    thread.replaceChildren();
    for (const m of data.messages) {
      if (m.role === "user") thread.append(el("p", "turn-user", m.text));
      else addMachine(m.text);
    }
    renderUnderstood(data.goal);
    renderAgents(data.agents || []);
  }

  $("#open-goals").addEventListener("click", async () => {
    const state = await api("/api/state");
    const nodes = state.goals.length ? state.goals.map((g) => {
      const b = el("button", "pick");
      b.type = "button";
      const meta = el("span", "pick-meta", g.status === "active" ? "" : g.status);
      b.append(el("span", "pick-title", g.title), meta);
      b.addEventListener("click", () => openGoal(g.id));
      return b;
    }) : [el("p", "empty", "Nothing yet. Say what you need.")];
    openDrawer(nodes);
  });

  $("#my-agents").addEventListener("click", async () => {
    const data = await api("/api/agents");
    const nodes = data.agents.length ? data.agents.map((a) => {
      const b = el("button", "pick");
      b.type = "button";
      const w = W(a.lang);
      const meta = el("span", "pick-meta");
      meta.append(stateBadge(a, w));
      if (a.schedule && a.schedule !== "once") meta.append(document.createTextNode(a.schedule));
      b.append(el("span", "pick-title", a.name), meta);
      if (a.runs && a.runs[0]) b.append(el("span", "pick-meta", a.runs[0].summary || ""));
      b.addEventListener("click", () => openGoal(a.goal_id));
      return b;
    }) : [el("p", "empty", "No agents yet. Say what you want done automatically.")];
    openDrawer(nodes);
  });

  $("#new-chat").addEventListener("click", async () => {
    try { await api("/api/new-chat", { method: "POST" }); } catch (_) { /* reload anyway */ }
    window.location.reload();
  });

  // ---------------------------------------------------------------- start
  (async () => {
    autosize();
    typeExamples();
    input.focus();
    try {
      const state = await api("/api/state");
      if (state.messages.length) {
        setActive();
        for (const m of state.messages) {
          if (m.role === "user") thread.append(el("p", "turn-user", m.text));
          else addMachine(m.text);
        }
        if (state.current) {
          currentGoalId = state.current.id;
          lastLang = state.current.detected_language === "English" ? "en" : "hi";
          renderUnderstood(state.current);
        }
        renderAgents(state.agents || []);
        typeExamples();
      }
    } catch (_) { /* offline first load: the page still works */ }
    refreshCount();
  })();
})();
