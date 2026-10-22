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
    if (agent && data.relation === "agent_plan" && agent.blocks.length) t.push(hi ? `${agent.blocks.length} blocks se agent banaya` : `Built an agent from ${agent.blocks.length} blocks`);
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

  // ---- the three ways in, chosen by one question
  function phpPanel(a, hi, c) {
    const box = el("div", "slot inner");
    const step = (n, text) => { const p = el("p", "step"); p.append(el("b", null, `${n}`), el("span", null, text)); return p; };
    const dl = el("button", "btn solid", T(hi, "File download karo", "Download the file")); dl.type = "button";
    const allowRow = el("label", "check");
    const allow = el("input"); allow.type = "checkbox";
    allowRow.append(allow, el("span", null, T(hi, "Approved changes bhi allow karo (har change pehle preview, Undo ke saath). Band rakhna sabse safe hai.",
                                                  "Also allow approved changes (each previewed first, with Undo). Leaving this off is safest.")));
    const url = el("input"); url.type = "url"; url.placeholder = "https://aapki-site.com/goal-connector-….php";
    const check = el("button", "btn go", T(hi, "Jodo", "Connect")); check.type = "submit";
    const err = el("p", "note err"); err.hidden = true;
    const form = el("form", "row"); form.append(url, check);
    if (c && c.file_url) url.value = c.file_url;
    if (c && c.status === "missing") {
      const warn = el("div", "missing");
      warn.append(
        el("b", null, T(hi, "File nahi mil rahi", "The file isn't answering")),
        el("p", null, T(hi, "Aksar hosting ka security scanner (Imunify360, Wordfence, Sucuri) nayi PHP files hata deta hai, ya firewall rok deta hai. Aapka data safe hai.",
                            "Hosting security scanners (Imunify360, Wordfence, Sucuri) often remove new PHP files, or a firewall blocks them. Your data is safe.")),
        el("p", null, T(hi, `Theek karne ke liye: 1) Neeche se file dobara download karke upload karo. 2) Scanner mein "${c.file_name}" ko allow / ignore list mein daalo. 3) Jodo dabao.`,
                            `To fix: 1) Download the file again below and upload it. 2) Add "${c.file_name}" to the scanner's allow / ignore list. 3) Tap Connect.`)));
      box.append(warn);
    }
    box.append(
      step(1, T(hi, "Ye file download karo (har database ke liye alag, isme ek secret key hai):", "Download this file (one per database; it has its own secret key):")),
      dl, allowRow,
      step(2, T(hi, "Hosting ke File Manager mein website ke main folder mein upload karo, jahan wp-config.php ya .env hai.",
                    "Upload it with your hosting's File Manager into the website's main folder, where wp-config.php or .env is.")),
      step(3, T(hi, "Upload ki hui file ka link yahan paste karo:", "Paste the link of the uploaded file here:")),
      form, err,
      el("p", "fine", T(hi, "Password aapki hosting pe hi rehta hai. File sirf Goal Engine ki signed requests maanti hai aur kuch badal nahi sakti. Disconnect karne ke liye file delete kar do.",
                            "Your password stays on your hosting. The file only accepts signed requests from Goal Engine and can't change anything. Delete the file to disconnect.")));
    let cid = c ? c.id : null;
    dl.addEventListener("click", async () => {
      dl.disabled = true;
      try {
        if (!cid) {
          const data = await api(`/api/agents/${a.id}/php-connector`, { method: "POST" });
          cid = (data.agents.find((x) => x.id === a.id) || {}).connector.id;
        }
        window.location.href = `/api/connectors/${cid}/php-file?changes=${allow.checked ? 1 : 0}`;
        dl.textContent = T(hi, "Download ho gaya. Dobara?", "Downloaded. Again?");
        url.focus();
      } catch (ex) { addMachine(ex.message, [], true); }
      dl.disabled = false;
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!cid) { err.textContent = T(hi, "Pehle file download karo (step 1).", "Download the file first (step 1)."); err.hidden = false; return; }
      check.disabled = true; check.textContent = T(hi, "Check kar raha hoon…", "Checking…"); err.hidden = true;
      try {
        await api(`/api/connectors/${cid}/php-link`, { method: "POST", body: JSON.stringify({ url: url.value }) });
        addMachine(T(hi, "Website ka database jud gaya. Tables padh raha hoon.", "Your website's database is connected. Reading the tables."),
                   [T(hi, "Signed, read-only connection", "Signed, read-only connection")]);
        await refreshGoal();
      } catch (ex) {
        err.textContent = ex.message; err.hidden = false;
        check.disabled = false; check.textContent = T(hi, "Jodo", "Connect");
      }
    });
    return box;
  }

  function formPanel(a, hi, onUseCommand) {
    const form = el("form", "dbform inner");
    const field = (label, input) => { const l = el("label", "f"); l.append(el("span", null, label), input); return l; };
    const type = el("select");
    for (const [v, t] of [["mysql", "MySQL / MariaDB"], ["postgres", "PostgreSQL"]]) { const o = el("option", null, t); o.value = v; type.append(o); }
    const host = el("input"); host.placeholder = T(hi, "jaise db.provider.com", "e.g. db.provider.com"); host.autocomplete = "off"; host.required = true;
    const port = el("input"); port.inputMode = "numeric"; port.placeholder = "3306"; port.maxLength = 5;
    type.addEventListener("change", () => { port.placeholder = type.value === "postgres" ? "5432" : "3306"; });
    const dbn = el("input"); dbn.autocomplete = "off"; dbn.required = true;
    const user = el("input"); user.autocomplete = "off"; user.required = true;
    const pass = el("input"); pass.type = "password"; pass.autocomplete = "new-password";
    const r1 = el("div", "row two"); r1.append(field(T(hi, "Database", "Database"), type), field("Port", port));
    const r2 = el("div", "row two"); r2.append(field(T(hi, "Database ka naam", "Database name"), dbn), field(T(hi, "User", "User"), user));
    const allowRow = el("label", "check");
    const allow = el("input"); allow.type = "checkbox";
    allowRow.append(allow, el("span", null, T(hi, "Approved changes allow karo (preview + Undo). Iske liye login ko data badalne ka haq chahiye.",
                                                  "Allow approved changes (preview + Undo). The login must be able to change data.")));
    form.append(r1, field("Host", host), r2, field(T(hi, "Password", "Password"), pass), allowRow);
    const ipHint = el("p", "fine", T(hi, "Sirf-SELECT wala user sabse safe hai. Password encrypt karke rakha jaata hai.", "A SELECT-only user is safest. The password is stored encrypted."));
    api("/api/connector-info").then((d) => {
      if (d.server_ip) ipHint.textContent += T(hi, ` Provider ke "allowed IPs" mein ye daalo: ${d.server_ip}`, ` Add this to your provider's allowed IPs: ${d.server_ip}`);
    }).catch(() => {});
    const go = el("button", "btn solid", T(hi, "Jodo", "Connect")); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true;
    const ga = el("div", "acts flat"); ga.append(go);
    form.append(ipHint, ga, err);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      go.disabled = true; go.textContent = T(hi, "Jod raha hoon…", "Connecting…"); err.hidden = true;
      try {
        const data = await api("/api/connectors/direct", { method: "POST", body: JSON.stringify({
          type: type.value, host: host.value, port: port.value, database: dbn.value, user: user.value, password: pass.value, agent_id: a.id,
          allow_changes: allow.checked }) });
        pass.value = "";
        addMachine(T(hi, "Database jud gaya. Tables padh raha hoon.", "Database connected. Reading the tables."),
                   [allow.checked ? T(hi, "Connection bana: padhna + approved changes", "Connected: read + approved changes") : T(hi, "Read-only connection bana", "Read-only connection made")]);
        renderAgents(data.agents);
      } catch (ex) {
        err.textContent = ex.message; err.hidden = false;
        go.disabled = false; go.textContent = T(hi, "Jodo", "Connect");
        if (ex.data && ex.data.use_command && onUseCommand) onUseCommand();
      }
    });
    return form;
  }

  function vpsPanel(a, hi) {
    const box = el("div", "slot inner");
    const make = el("button", "btn solid", T(hi, "Command banao", "Get the command")); make.type = "button";
    box.append(el("p", "fine", T(hi, "Server ke terminal mein ek command paste karni hai. Ye database khud dhoondh lega.",
                                     "You'll paste one command in your server's terminal. It finds the database by itself.")), make);
    make.addEventListener("click", async () => {
      make.disabled = true;
      try {
        const data = await api("/api/connectors", { method: "POST", body: JSON.stringify({ agent_id: a.id }) });
        box.replaceChildren(
          el("label", null, T(hi, "Ye ek command server ke terminal mein paste karo. Ye sirf ek baar dikhega:", "Paste this one command in your server's terminal. It's shown only once:")),
          codeBox("", data.command, W(a.lang)),
          el("p", "fine", T(hi, "List mein se database ka number chuno aur Enter dabao. Password aapke server pe hi rehta hai.",
                                "Pick your database's number from the list and press Enter. The password stays on your server.")),
          el("p", "waiting", T(hi, "Connector ka intezaar…", "Waiting for the connector…")));
        watchConnector(data.connector.id, a.id);
      } catch (ex) { addMachine(ex.message, [], true); make.disabled = false; }
    });
    return box;
  }

  function setupBox(a, hi) {
    const box = el("div", "slot");
    if (a.connectors && a.connectors.some((c) => c.online)) {
      box.append(el("label", null, T(hi, "Pehle se juda database use karo", "Use a database you already connected")));
      const row = el("div", "acts flat");
      for (const c of a.connectors.filter((x) => x.online)) {
        const b = el("button", "btn", `${c.name}${c.db_name && c.db_name !== c.name ? ` · ${c.db_name}` : ""}`);
        b.type = "button";
        b.addEventListener("click", async () => renderAgents((await api(`/api/agents/${a.id}/connect/${c.id}`, { method: "POST" })).agents));
        row.append(b);
      }
      box.append(row);
    }
    box.append(el("p", "ask", T(hi, "Aapka database kahan hai?", "Where is your database?")));
    const choices = el("div", "where");
    const panel = el("div", "where-panel");
    const options = [
      ["hosting", T(hi, "Website hosting", "Website hosting"), T(hi, "cPanel, Hostinger, WordPress, PHP site · terminal nahi chahiye", "cPanel, Hostinger, WordPress, any PHP site · no terminal")],
      ["cloud", T(hi, "Cloud database", "Cloud database"), T(hi, "AWS, DigitalOcean, Supabase, Neon · bas details bharo", "AWS, DigitalOcean, Supabase, Neon · just fill the details")],
      ["vps", T(hi, "Apna server / VPS", "My own server / VPS"), T(hi, "Terminal access hai · ek command", "You have a terminal · one command")],
      ["windows", T(hi, "Windows PC / XAMPP", "Windows PC / XAMPP"), T(hi, "Jald aa raha", "Coming soon")],
    ];
    const pick = (key) => {
      for (const b of choices.children) b.setAttribute("aria-pressed", String(b.dataset.key === key));
      panel.replaceChildren();
      if (key === "hosting") panel.append(phpPanel(a, hi, null));
      else if (key === "cloud") panel.append(formPanel(a, hi, () => pick("vps")));
      else if (key === "vps") panel.append(vpsPanel(a, hi));
      else panel.append(el("p", "fine", T(hi, "Windows connector ban raha hai. Agar aapki website kisi hosting pe hai to \"Website hosting\" chuno, wo abhi kaam karega.",
                                             "The Windows connector is being built. If your website is on hosting, choose \"Website hosting\"; that works now.")));
    };
    for (const [key, title, sub] of options) {
      const b = el("button", "choice"); b.type = "button"; b.dataset.key = key; b.setAttribute("aria-pressed", "false");
      b.append(el("b", null, title), el("span", null, sub));
      b.addEventListener("click", () => pick(key));
      choices.append(b);
    }
    box.append(choices, panel);
    return box;
  }

  // ---- live view and activity log (refresh only while open and the page is visible)
  const dbTab = {};
  let watchTimer = null;
  let watchGen = 0;
  function stopWatchers() { clearTimeout(watchTimer); watchGen++; }

  function loop(fn, every) {
    const gen = watchGen;
    const tick = async () => {
      if (gen !== watchGen) return;
      if (!document.hidden) { try { await fn(); } catch (_) { /* next tick */ } }
      if (gen === watchGen) watchTimer = setTimeout(tick, every);
    };
    tick();
  }

  function section(title, data, hi, empty) {
    const box = el("div", "live-sec");
    box.append(el("p", "cap", title));
    if (!data || !data.available) { box.append(el("p", "fine", data && data.reason ? data.reason : T(hi, "Uplabdh nahi", "Not available"))); return box; }
    if (!data.rows.length) { box.append(el("p", "fine", empty)); return box; }
    box.append(grid(data));
    return box;
  }

  function startLive(c, hi, paneEl) {
    paneEl.replaceChildren(el("p", "waiting", T(hi, "Database se live judaa raha hoon…", "Connecting live…")));
    let last = null;
    loop(async () => {
      const j = await runJob(c.id, { kind: "live" });
      const r = j.result || {};
      const parts = [];
      const st = (r.status && r.status.available) ? r.status.values : null;
      if (st) {
        const strip = el("div", "stats");
        const q = Number(st.Questions || st.commits || 0);
        const t = Date.parse(r.at);
        if (last && t > last.t) {
          const qps = Math.max(0, (q - last.q) / ((t - last.t) / 1000));
          strip.append(stat(qps.toFixed(1), T(hi, "queries/sec", "queries/sec")));
        }
        last = { q, t };
        for (const [k, label] of [["Threads_connected", "connections"], ["connections", "connections"], ["Threads_running", "running"],
                                  ["active", "running"], ["Slow_queries", "slow so far"], ["deadlocks", "deadlocks"], ["cache_hit_%", "cache hit %"]]) {
          if (st[k] !== undefined) strip.append(stat(String(st[k]), label));
        }
        parts.push(strip);
      }
      parts.push(section(T(hi, "Abhi chal rahi queries", "Running now"), r.running, hi, T(hi, "Abhi koi query nahi chal rahi.", "Nothing running right now.")));
      parts.push(section(T(hi, "Sabse zyada time lene wali queries", "Queries taking the most time"), r.top, hi, T(hi, "Abhi data nahi.", "No data yet.")));
      parts.push(section(T(hi, "Slow query log", "Slow query log"), r.slow, hi, T(hi, "Koi slow query nahi.", "No slow queries.")));
      parts.push(section(T(hi, "Database errors", "Database errors"), r.errors, hi, T(hi, "Koi error nahi.", "No errors.")));
      parts.push(el("p", "fine", T(hi, "Har 3 second mein update. Queries ki values chhupi hain (?), aur kuch save nahi hota.",
                                       "Updates every 3 seconds. Values in queries are hidden (?), and nothing is saved.")));
      paneEl.replaceChildren(...parts);
    }, 3000);
  }

  function stat(value, label) {
    const d = el("div", "stat");
    d.append(el("b", null, value), el("span", null, label));
    return d;
  }

  function startActivity(c, hi, paneEl) {
    paneEl.replaceChildren(el("p", "waiting", T(hi, "Activity la raha hoon…", "Loading activity…")));
    loop(async () => {
      const d = await api(`/api/connectors/${c.id}/activity`);
      if (!d.items.length) { paneEl.replaceChildren(el("p", "fine", T(hi, "Abhi tak kuch nahi hua.", "Nothing yet."))); return; }
      const ul = el("ul", "activity");
      for (const it of d.items) {
        const li = el("li", `act ${it.status}`);
        const when = new Date(`${it.at}Z`).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        const meta = [it.rows !== null && it.rows !== undefined ? `${Number(it.rows).toLocaleString("en-IN")} rows` : "",
                      it.ms !== null && it.ms !== undefined ? `${it.ms} ms` : "",
                      it.status === "failed" ? (it.error || "failed") : it.status === "done" ? "" : it.status].filter(Boolean).join(" · ");
        li.append(el("time", null, when), el("span", "what", it.summary), el("span", "meta", meta));
        if (it.undo) li.append(undoButton(c, it, hi));
        ul.append(li);
      }
      paneEl.replaceChildren(ul, el("p", "fine", T(hi, "Live update. Values chhupi hain; 30 din tak rakha jaata hai.", "Updates live. Values are hidden; kept for 30 days.")));
    }, 3000);
  }

  async function doUndo(c, changeId, hi, force, onDone) {
    const j0 = await api(`/api/connectors/${c.id}/undo`, { method: "POST", body: JSON.stringify({ change_id: changeId, force }) });
    for (let i = 0; i < 90; i++) {
      await sleep(i < 4 ? 600 : 1200);
      const j = await api(`/api/connector-jobs/${j0.id}`);
      if (j.status === "done") { onDone(null, j); return; }
      if (j.status === "failed" || j.status === "expired") { onDone(j.error || "Undo failed", j); return; }
    }
    onDone(T(hi, "Undo ka jawab nahi aaya.", "The undo didn't answer."), null);
  }

  function undoButton(c, it, hi) {
    const box = el("span", "undo");
    if (it.undo.undone) { box.append(el("em", null, T(hi, "Undo ho gaya", "Undone"))); return box; }
    if (!it.undo.available) return box;
    const b = el("button", "tdl", "Undo"); b.type = "button";
    b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = T(hi, "Undo ho raha hai…", "Undoing…");
      const run = (force) => doUndo(c, it.id, hi, force, (err) => {
        if (!err) { b.replaceWith(el("em", null, T(hi, "Undo ho gaya", "Undone"))); return; }
        if (err.startsWith("CONFLICT")) {
          const msg = el("span", "note err", err.replace(/^CONFLICT:\s*/, ""));
          const f = el("button", "tdl danger", T(hi, "Phir bhi undo karo", "Undo anyway")); f.type = "button";
          f.addEventListener("click", () => { f.disabled = true; run(true); });
          b.replaceWith(msg, f);
        } else { b.textContent = "Undo"; b.disabled = false; addMachine(err, [], true); }
      });
      try { await run(false); } catch (ex) { addMachine(ex.message, [], true); b.disabled = false; b.textContent = "Undo"; }
    });
    box.append(b);
    return box;
  }

  // Panels with work in progress survive the card being redrawn (e.g. when the table list arrives)
  const kept = new Map();
  function keep(key, make) {
    if (!kept.has(key)) kept.set(key, make());
    return kept.get(key);
  }

  // ---- the database brain: problem -> checks -> causes -> proven fix
  async function waitJob(id) {
    for (let i = 0; i < 120; i++) {
      const j = await api(`/api/connector-jobs/${id}`);
      if (j.status === "done") return j;
      if (j.status === "failed" || j.status === "expired") throw new Error(j.error || "The check didn't finish");
      await sleep(i < 5 ? 500 : 1200);
    }
    throw new Error("The database isn't answering. Try again in a moment.");
  }

  const ACTION = { approved_change: ["Agent kar sakta hai", "Agent can apply"], config: ["Setting badlo", "Setting change"],
                   code: ["Code mein badlav", "Code change"], advice: ["Salah", "Advice"] };

  function brainPanel(c, a, hi) {
    const box = el("section", "brain");
    const body2 = el("div", "brain-body");
    const ask = el("form", "brain-ask");
    const q = el("input"); q.placeholder = T(hi, "Database ki problem likho, error paste karo, ya slow query daalo", "Describe the problem, paste an error or a slow query");
    const go = el("button", "btn solid", T(hi, "Dekho", "Check")); go.type = "submit";
    ask.append(q, go);
    ask.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!q.value.trim()) return;
      try {
        const r = await api("/api/dbbrain/recognize", { method: "POST", body: JSON.stringify({ text: q.value, lang: hi ? "hi" : "en" }) });
        if (r.kind !== "sql" && r.confidence < 0.6) choose(r, q.value);
        else show({ ...r, sql: r.kind === "sql" ? q.value : "" });
      } catch (ex) { body2.replaceChildren(el("p", "note err", ex.message)); }
    });
    box.append(ask, body2);

    const show = (b) => {
      body2.replaceChildren();
      if (!b || !b.kind || (b.kind === "question")) {
        if (b && b.kind === "question") body2.append(el("p", "fine", T(hi, "Data ke sawaal jald aa rahe hain. Abhi Tables ya SQL box use karo.", "Questions about your data are coming soon. Use Tables or the SQL box for now.")));
        return;
      }
      if (b.text) body2.append(el("p", "brain-seen", (hi ? "Samjha: " : "Understood: ") + b.text));
      if (b.kind === "issue") issueFlow(b.id);
      else if (b.kind === "error") errorFlow(b.id);
      else if (b.kind === "sql" || b.id === "slow_query") queryFlow(b.sql || "");
      else if (b.id === "health") body2.append(el("p", "fine", T(hi, "Neeche Health check dabao.", "Use Health check below.")));
    };

    // Not sure: the person picks the meaning. Their pick teaches the brain (3 different people = learned).
    const choose = (r, text) => {
      body2.replaceChildren(el("p", "brain-seen", T(hi, "Aapka matlab inmein se kaunsa hai?", "Which of these do you mean?")));
      const row = el("div", "brain-choices");
      for (const o of [r, ...(r.alternatives || [])].filter((x) => x.text).slice(0, 4)) {
        const b = el("button", "chip", o.text); b.type = "button";
        b.addEventListener("click", () => {
          api("/api/learn/choice", { method: "POST", body: JSON.stringify({ brain: "database", text, label: `${o.kind}:${o.id}` }) }).catch(() => {});
          show({ ...o, sql: "" });
        });
        row.append(b);
      }
      const none = el("button", "chip ghost", T(hi, "Inmein se koi nahi", "None of these")); none.type = "button";
      none.addEventListener("click", async () => {
        try { await api("/api/learn/ask", { method: "POST", body: JSON.stringify({ brain: "database", text, guess: `${r.kind}:${r.id}`, confidence: r.confidence }) }); } catch (_) { /* still fine */ }
        body2.replaceChildren(el("p", "fine", T(hi, "Note kar liya. Agent ise seekh raha hai. Tab tak Health check chalao, ya expert se baat karo.",
          "Noted. The agent is learning this one. Meanwhile run Health check, or talk to an expert.")));
      });
      row.append(none);
      body2.append(row, el("p", "fine small", T(hi, "Aapka jawab agent ko sikhata hai. Sirf sawaal ki line rakhi jaati hai, bina naam, email ya number ke.",
        "Your pick teaches the agent. Only the sentence is kept, without names, emails or numbers.")));
    };

    const issueFlow = (id) => {
      const run = el("button", "btn go", T(hi, "Mere database pe check karo", "Check my database")); run.type = "button";
      const out = el("div", "brain-out");
      body2.append(run, out);
      run.addEventListener("click", async () => {
        run.disabled = true; out.replaceChildren(el("p", "waiting", T(hi, "Database check kar raha hoon…", "Checking your database…")));
        try {
          const d = await api(`/api/connectors/${c.id}/diagnose`, { method: "POST", body: JSON.stringify({ issue_id: id }) });
          const j = await waitJob(d.job.id);
          const parts = [el("p", "cap", T(hi, "Jo maine dekha", "What I found"))];
          j.result.results.forEach((r, i) => {
            const chk = el("div", "chk");
            chk.append(el("p", "chk-hint", d.checks[i].look_for));
            chk.append(r.error ? el("p", "fine", r.error) : grid(r));
            parts.push(chk);
          });
          parts.push(el("p", "cap", T(hi, "Ho sakti wajah aur fix", "Likely causes and fixes")));
          const ul = el("ul", "causes");
          for (const cause of d.playbook.causes) {
            const li = el("li");
            li.append(el("b", null, hi ? cause.title_hi : cause.title_en));
            for (const f of d.playbook.fixes.filter((x) => x.cause === cause.id)) {
              const fx = el("div", "fix");
              fx.append(el("span", `tag ${f.action}`, (ACTION[f.action] || ["", ""])[hi ? 0 : 1]), el("span", null, hi ? f.steps_hi : f.steps_en));
              li.append(fx);
            }
            ul.append(li);
          }
          parts.push(ul, el("p", "fine", (hi ? "Theek hua ya nahi kaise pata chalega: " : "How to know it's fixed: ") + (hi ? d.playbook.verify.hi : d.playbook.verify.en)));
          out.replaceChildren(...parts);
        } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
        run.disabled = false; run.textContent = T(hi, "Dobara check karo", "Check again");
      });
    };

    const errorFlow = async (id) => {
      try {
        const e = await api(`/api/dbbrain/playbook/${id}`);
        const fx = el("div", "fix");
        fx.append(el("span", "tag advice", T(hi, "Fix", "Fix")), el("span", null, e.fix.note || ""));
        body2.append(el("p", null, hi ? e.explain_hi : e.explain_en), fx);
      } catch (_) { /* nothing more to show */ }
    };

    const queryFlow = (sql) => {
      const ta = el("textarea", "brain-sql"); ta.value = sql; ta.placeholder = "SELECT … FROM … WHERE …";
      const run = el("button", "btn go", T(hi, "Mere database pe test karo", "Test it on my database")); run.type = "button";
      const out = el("div", "brain-out");
      body2.append(ta, run, out);
      const test = async (before) => {
        out.replaceChildren(el("p", "waiting", T(hi, "Fixes try kar raha hoon aur naap raha hoon…", "Trying fixes and measuring…")));
        const d = await api(`/api/connectors/${c.id}/optimize`, { method: "POST", body: JSON.stringify({ sql: ta.value }) });
        const j = await waitJob(d.job.id);
        const r = j.result, an = d.analysis;
        const parts = [];
        if (before) {
          const n = (v) => (typeof v === "number" ? v.toLocaleString("en-IN") : v);
          const unit = (v) => (r.original.metric === "rows" ? (v === 1 ? T(hi, "row padhi", "row read") : T(hi, "rows padhi", "rows read")) : "cost");
          parts.push(el("p", "brain-win", T(hi, `Pehle: ${before.ms} ms, ${n(before.plan)} ${unit(before.plan)}  →  Ab: ${r.original.ms} ms, ${n(r.original.plan)} ${unit(r.original.plan)}`,
                                                 `Before: ${before.ms} ms, ${n(before.plan)} ${unit(before.plan)}  →  Now: ${r.original.ms} ms, ${n(r.original.plan)} ${unit(r.original.plan)}`)));
        }
        const rows = [[T(hi, "Aapki query", "Your query"), "—", r.original.ms + " ms", r.original.plan ?? "—"]];
        for (const cnd of r.candidates) {
          const same = cnd.same === true ? T(hi, "✓ same result", "✓ same result") : cnd.same === "count" ? T(hi, "≈ same count", "≈ same count") : cnd.same === false ? T(hi, "✗ alag result", "✗ different result") : "—";
          rows.push([T(hi, "Rewrite", "Rewrite"), same, cnd.ms + " ms", cnd.plan ?? "—"]);
        }
        parts.push(el("p", "cap", T(hi, "Aapke database pe naapa", "Measured on your database")),
                   grid({ columns: ["", T(hi, "Result", "Result"), T(hi, "Time", "Time"), r.original.metric === "rows" ? T(hi, "Rows padhi", "Rows read") : "Cost"], rows }));
        const best = r.candidates.find((x) => x.same === true || x.same === "count");
        if (best && an.rewritten) parts.push(codeBox(T(hi, "Behtar query (same result)", "Better query (same result)"), an.rewritten, W(a.lang)));
        const why = [...an.findings.map((f) => (hi ? f.title_hi : f.title_en)), ...an.extra.map((f) => (hi ? f.title_hi : f.title_en))];
        if (why.length) { const ul = el("ul", "why"); for (const w2 of [...new Set(why)]) ul.append(el("li", null, w2)); parts.push(el("p", "cap", T(hi, "Slow kyun hai", "Why it's slow")), ul); }
        for (const ix of an.indexes) {
          const row = el("div", "fix");
          const m = ix.match(/(?:alter\s+table\s+(\S+)\s+add\s+index\s+\S+|create\s+index\s+\S+\s+on\s+(\S+))\s*\(([^)]+)\)/i);
          const tname = m ? (m[1] || m[2]).replace(/[`"]/g, "") : "";
          const cols = m ? m[3].split(",").map((x) => x.trim().replace(/[`"]/g, "")) : [];
          const t = r.tables[tname] || Object.values(r.tables)[0];
          const covered = t && t.indexes && t.indexes.some((i) => cols.length && i.columns.slice(0, cols.length).join(",") === cols.join(","));
          if (covered) {
            row.append(el("span", "tag approved_change", "Index"), el("span", null, T(hi, `✓ ${tname} pe ye index pehle se laga hai`, `✓ This index is already on ${tname}`)));
            parts.push(row);
            continue;
          }
          row.append(el("span", "tag approved_change", "Index"), el("code", null, ix));
          if (c.allow_changes) {
            const fixb = el("button", "btn go", T(hi, "Fix karo", "Fix it")); fixb.type = "button";
            fixb.addEventListener("click", async () => {
              fixb.disabled = true; fixb.textContent = T(hi, "Index bana raha hoon…", "Building the index…");
              try {
                const p0 = await api(`/api/connectors/${c.id}/plan`, { method: "POST", body: JSON.stringify({ sql: ix }) });
                const p = await waitJob(p0.id);
                if (p.result.blocked) throw new Error(p.result.blocked);
                const ch0 = await api(`/api/connectors/${c.id}/apply`, { method: "POST", body: JSON.stringify({ plan_id: p.id }) });
                const ch = await waitJob(ch0.id);
                await test(r.original);
                out.prepend(undoButton(c, { id: ch.id, undo: { available: true } }, hi));
              } catch (ex) { fixb.disabled = false; fixb.textContent = T(hi, "Fix karo", "Fix it"); addMachine(ex.message, [], true); }
            });
            row.append(fixb);
          } else {
            row.append(el("span", "fine", T(hi, "(Fix karne ke liye is database pe approved changes on karo)", "(turn on approved changes for this database to apply it)")));
          }
          if (t && t.rows !== undefined) row.append(el("span", "fine", T(hi, `${t.rows.toLocaleString("en-IN")} rows`, `${t.rows.toLocaleString("en-IN")} rows`)));
          parts.push(row);
        }
        out.replaceChildren(...parts);
      };
      run.addEventListener("click", async () => {
        run.disabled = true;
        try { await test(null); } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
        run.disabled = false;
      });
    };

    show(a.brain);
    return box;
  }

  // ---- approved changes: preview -> apply -> undo
  function changesBox(c, a, hi) {
    const box = el("details", "db-sql changes");
    box.append(el("summary", null, T(hi, "Data badlo (pehle preview, phir Apply, Undo hamesha)", "Change data (preview first, then Apply, always undoable)")));
    if (!c.allow_changes) {
      const how = c.kind === "php"
        ? T(hi, "Is website file mein changes band hain. Changes wali file download karke purani file ki jagah upload karo, phir \"Dobara check karo\" dabao.",
                "Changes are off in this website file. Download the file with changes on, upload it over the old one, then tap \"Check again\".")
        : c.kind === "direct"
          ? T(hi, "Changes band hain. Disconnect karke dobara jodo aur \"Approved changes allow karo\" tick karo (login ko data badalne ka haq chahiye).",
              "Changes are off. Disconnect and connect again with \"Allow approved changes\" ticked (the login must be able to change data).")
          : T(hi, "Changes band hain. Server pe connector ka setup dobara chalao aur \"Allow approved changes\" pe y dabao.",
              "Changes are off. Run the connector setup again on your server and answer y to \"Allow approved changes\".");
      box.append(el("p", "fine", how));
      if (c.kind === "php") {
        const row = el("div", "acts flat");
        const dl = el("button", "btn", T(hi, "Changes wali file download karo", "Download the file with changes on")); dl.type = "button";
        dl.addEventListener("click", () => { window.location.href = `/api/connectors/${c.id}/php-file?changes=1`; });
        const again = el("button", "btn solid", T(hi, "Dobara check karo", "Check again")); again.type = "button";
        again.addEventListener("click", async () => {
          again.disabled = true;
          try { await api(`/api/connectors/${c.id}/php-link`, { method: "POST", body: JSON.stringify({ url: c.file_url }) }); await refreshGoal(); }
          catch (ex) { addMachine(ex.message, [], true); again.disabled = false; }
        });
        row.append(dl, again);
        box.append(row);
      }
      return box;
    }
    const ta = el("textarea"); ta.placeholder = "UPDATE orders SET status = 'paid' WHERE id = 42";
    const result = el("div", "change-out");
    const prev = el("button", "btn solid", T(hi, "Preview", "Preview")); prev.type = "button";
    const row = el("div", "acts flat"); row.append(prev);
    box.append(el("p", "fine", T(hi, "Ek table, ek statement: UPDATE … WHERE, DELETE FROM … WHERE, ya INSERT INTO … VALUES. Ek baar mein 1000 rows tak.",
                                    "One table, one statement: UPDATE … WHERE, DELETE FROM … WHERE, or INSERT INTO … VALUES. Up to 1000 rows at a time.")), ta, row, result);
    prev.addEventListener("click", async () => {
      result.replaceChildren(el("p", "waiting", T(hi, "Dekh raha hoon kaun si rows badlengi…", "Finding the rows this would change…")));
      try {
        const p0 = await api(`/api/connectors/${c.id}/plan`, { method: "POST", body: JSON.stringify({ sql: ta.value }) });
        let p = p0;
        for (let i = 0; i < 90 && !["done", "failed", "expired"].includes(p.status); i++) { await sleep(i < 4 ? 500 : 1200); p = await api(`/api/connector-jobs/${p0.id}`); }
        if (p.status !== "done") throw new Error(p.error || "Preview failed");
        const r = p.result;
        const parts = [];
        const what = r.op === "insert" ? T(hi, `${r.table} mein nayi rows judengi`, `New rows will be added to ${r.table}`)
          : T(hi, `${r.count} rows ${r.op === "delete" ? "delete hongi" : "badlengi"} · ${r.table}`, `${r.count} rows will be ${r.op === "delete" ? "deleted" : "changed"} · ${r.table}`);
        parts.push(el("p", "change-what", what));
        if (r.rows && r.rows.length) parts.push(el("p", "cap", T(hi, "Abhi ye rows aisi hain (pehli 20):", "These rows as they are now (first 20):")), grid({ columns: r.columns, rows: r.rows }));
        if (r.warning) parts.push(el("p", "note warn", r.warning));
        if (r.blocked) { parts.push(el("p", "note err", r.blocked)); result.replaceChildren(...parts); return; }
        if (r.op !== "insert" && !r.count) { parts.push(el("p", "fine", T(hi, "Koi row match nahi hui, kuch nahi badlega.", "No rows match; nothing would change."))); result.replaceChildren(...parts); return; }
        const apply = el("button", "btn go", T(hi, "Apply karo", "Apply")); apply.type = "button";
        const cancel = el("button", "btn", T(hi, "Rehne do", "Cancel")); cancel.type = "button";
        cancel.addEventListener("click", () => result.replaceChildren());
        apply.addEventListener("click", async () => {
          apply.disabled = true; cancel.disabled = true; apply.textContent = T(hi, "Badal raha hoon…", "Applying…");
          try {
            const j0 = await api(`/api/connectors/${c.id}/apply`, { method: "POST", body: JSON.stringify({ plan_id: p.id }) });
            let j = j0;
            for (let i = 0; i < 90 && !["done", "failed", "expired"].includes(j.status); i++) { await sleep(i < 4 ? 500 : 1200); j = await api(`/api/connector-jobs/${j0.id}`); }
            if (j.status !== "done") throw new Error(j.error || "The change failed. Nothing was changed");
            const done = el("div", "change-done");
            done.append(el("p", null, T(hi, `${j.result.rows} rows badal di. Purani copy ${j.result.undo.where === "your server" ? "aapke server" : j.result.undo.where === "your hosting" ? "aapki hosting" : "encrypted"} pe hai; 7 din tak Undo kar sakte ho.`,
                                        `${j.result.rows} rows changed. The old copy is kept ${j.result.undo.where === "Goal Engine, encrypted" ? "encrypted" : "on " + j.result.undo.where}; you can Undo for 7 days.`)),
                        undoButton(c, { id: j.id, undo: { available: true } }, hi));
            result.replaceChildren(done);
            addMachine(T(hi, `${j.result.rows} rows badli (${j.result.table}). Undo card mein hai.`, `Changed ${j.result.rows} rows in ${j.result.table}. Undo is on the card.`),
                       [T(hi, "Purani rows save ki", "Saved the old rows"), T(hi, "Change apply kiya", "Applied the change")]);
          } catch (ex) { result.replaceChildren(el("p", "note err", ex.message)); }
        });
        const acts2 = el("div", "acts flat"); acts2.append(apply, cancel);
        parts.push(acts2);
        result.replaceChildren(...parts);
      } catch (ex) { result.replaceChildren(el("p", "note err", ex.message)); }
    });
    return box;
  }

  // ---------------------------------------------------------------- Microsoft agent: the person gives a file, the agent fixes it
  const OFFICE_CHUNK = 4 * 1024 * 1024;

  async function officeUpload(file, agentId, onProgress) {
    let f = await api("/api/office/files", { method: "POST", body: JSON.stringify({ name: file.name, size: file.size, agent_id: agentId }) });
    let offset = f.received || 0;
    let tries = 0;
    while (offset < file.size) {
      const piece = file.slice(offset, offset + OFFICE_CHUNK);
      try {
        const res = await fetch(`/api/office/files/${f.id}/chunk?offset=${offset}`, { method: "PUT", body: piece, credentials: "same-origin",
                                                                                     headers: { "Content-Type": "application/octet-stream" } });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "Upload failed");
        offset = d.received; tries = 0;
        onProgress(offset / file.size);
      } catch (ex) {
        if (++tries > 5) throw ex;                        // internet dropped: wait and continue from the same place
        await new Promise((r) => setTimeout(r, 1500 * tries));
      }
    }
    return api(`/api/office/files/${f.id}/done`, { method: "POST" });
  }

  async function officeWait(id, onTick) {
    for (;;) {
      const f = await api(`/api/office/files/${id}`);
      if (f.status === "ready" || f.status === "error") return f;
      onTick && onTick(f);
      await new Promise((r) => setTimeout(r, f.size > 50e6 ? 3000 : 1000));
    }
  }

  function officePanel(a, hi) {
    const box = el("section", "office");
    const out = el("div", "office-out");
    const pick = (label, multiple) => {
      const inp = el("input"); inp.type = "file"; inp.accept = ".xlsx,.xlsm,.csv"; inp.hidden = true; inp.multiple = !!multiple;
      const btn = el("button", "btn go", label); btn.type = "button";
      btn.addEventListener("click", () => inp.click());
      return [btn, inp];
    };
    const status = (text) => out.replaceChildren(el("p", "waiting", text));
    const bar = (frac, text) => {
      const p = el("div", "progress"); const i = el("span"); i.style.width = `${Math.round(frac * 100)}%`; p.append(i);
      out.replaceChildren(el("p", "waiting", `${text} ${Math.round(frac * 100)}%`), p);
    };

    const drop = el("div", "dropzone");
    const [btn, inp] = pick(T(hi, "File chuno", "Choose a file"));
    drop.append(el("p", null, T(hi, "Excel (.xlsx) ya CSV file yahan chhodo. 1.5 GB tak.", "Drop an Excel (.xlsx) or CSV file here. Up to 1.5 GB.")), btn, inp,
                el("p", "fine small", T(hi, "Original file kabhi nahi badalti; aapko theek ki hui copy milti hai. Files 1 ghante mein apne aap delete.",
                                         "Your original never changes; you get a fixed copy. Files are deleted automatically within an hour.")));
    const go = async (file) => {
      try {
        bar(0, T(hi, "Upload ho raha hai", "Uploading"));
        const f = await officeUpload(file, a.id, (x) => bar(x, T(hi, "Upload ho raha hai", "Uploading")));
        status(T(hi, "File check kar raha hoon…", "Checking the file…"));
        showFile(await officeWait(f.id));
      } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
    };
    inp.addEventListener("change", () => inp.files[0] && go(inp.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) go(e.dataTransfer.files[0]); });

    const FIND = [
      ["broken_refs", "fix_broken_refs", true, (n) => T(hi, `${n} formula toote hue hain (#REF!)`, `${n} broken formulas (#REF!)`)],
      ["text_numbers", "text_to_number", true, (n) => T(hi, `${n} number text ki tarah save hain (total galat aata hai)`, `${n} numbers saved as text (totals come out wrong)`)],
      ["spaces", "trim_spaces", true, (n) => T(hi, `${n} cells mein chhupe hue extra space`, `${n} cells with hidden extra spaces`)],
      ["duplicate_rows", "remove_duplicates", false, (n) => T(hi, `${n} rows do baar hain (duplicate)`, `${n} repeated rows (duplicates)`)],
      ["errors", null, false, (n) => T(hi, `${n} cells mein error dikh raha hai (#N/A, #DIV/0! …)`, `${n} cells show an error (#N/A, #DIV/0! …)`)],
      ["odd_formulas", null, false, (n) => T(hi, `${n} rows ka formula baaki column se alag hai (galti ho sakti hai)`, `${n} rows have a formula different from the rest of the column (possible mistake)`)],
    ];

    const cellsOf = (rep, key) => {
      const list = [];
      for (const sh of rep.sheets) {
        const cells = key === "broken_refs" ? sh.broken_refs : key === "odd_formulas" ? sh.odd_formulas : key === "text_numbers" ? sh.text_number_cells
          : key === "spaces" ? sh.space_cells : key === "errors" ? Object.values(sh.errors).flatMap((e) => e.cells) : [];
        for (const c of (cells || []).slice(0, 8)) list.push(rep.sheets.length > 1 ? `${sh.name}!${c}` : c);
      }
      return list.join(", ");
    };

    const showFile = (f) => {
      if (f.status === "error") { out.replaceChildren(el("p", "note err", f.error)); return; }
      if (f.result) { showResult(f); return; }
      const rep = f.report, s = rep.summary;
      const parts = [el("p", "cap", `${f.name} · ${s.sheets} ${T(hi, "sheet", s.sheets > 1 ? "sheets" : "sheet")} · ${(s.rows || 0).toLocaleString("en-IN")} ${T(hi, "rows", "rows")} · ${(s.formulas || 0).toLocaleString("en-IN")} ${T(hi, "formula", "formulas")}`)];
      const ops = [];
      const ul = el("ul", "findings");
      for (const [key, op, on, text] of FIND) {
        const n = s[key] || 0;
        if (!n) continue;
        const li = el("li");
        if (op) {
          const cb = el("input"); cb.type = "checkbox"; cb.checked = on; cb.id = `of-${f.id}-${op}`;
          const lb = el("label", null, text(n.toLocaleString("en-IN"))); lb.htmlFor = cb.id;
          li.append(cb, lb); ops.push([op, cb]);
        } else li.append(el("span", null, text(n.toLocaleString("en-IN"))));
        const where = cellsOf(rep, key);
        if (where) li.append(el("small", null, `${T(hi, "Kahan", "Where")}: ${where}${n > 8 ? " …" : ""}`));
        ul.append(li);
      }
      if (!ul.children.length) parts.push(el("p", "fine", T(hi, "Is file mein mujhe koi galti nahi mili.", "I found no problems in this file.")));
      else parts.push(ul);
      const acts = el("div", "acts");
      if (ops.length) {
        const fixBtn = el("button", "btn solid", T(hi, "Theek karo", "Fix it")); fixBtn.type = "button";
        fixBtn.addEventListener("click", async () => {
          const chosen = ops.filter(([, cb]) => cb.checked).map(([op]) => op);
          if (!chosen.length) return;
          try {
            await api(`/api/office/files/${f.id}/fix`, { method: "POST", body: JSON.stringify({ ops: chosen }) });
            status(T(hi, "Theek kar raha hoon…", "Fixing…"));
            showResult(await officeWait(f.id));
          } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
        });
        acts.append(fixBtn);
      }
      const [cmpBtn, cmpInp] = pick(T(hi, "Doosri file se compare karo", "Compare with another file"));
      cmpBtn.className = "btn";
      cmpInp.addEventListener("change", async () => {
        const other = cmpInp.files[0]; if (!other) return;
        try {
          bar(0, T(hi, "Doosri file upload", "Uploading the second file"));
          const g = await officeUpload(other, a.id, (x) => bar(x, T(hi, "Doosri file upload", "Uploading the second file")));
          status(T(hi, "Doosri file check kar raha hoon…", "Checking the second file…"));
          const g2 = await officeWait(g.id);
          if (g2.status === "error") { out.replaceChildren(el("p", "note err", g2.error)); return; }
          await api(`/api/office/files/${f.id}/compare`, { method: "POST", body: JSON.stringify({ other: g2.id }) });
          status(T(hi, "Dono files mila raha hoon…", "Comparing both files…"));
          showResult(await officeWait(f.id));
        } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
      });
      acts.append(cmpBtn, cmpInp);
      parts.push(acts);
      out.replaceChildren(...parts);
    };

    const showResult = (f) => {
      if (f.status === "error") { out.replaceChildren(el("p", "note err", f.error)); return; }
      const r = f.result, parts = [];
      if (r.task === "fix") {
        const c = r.changes || {};
        const lines = [
          [c.fix_broken_refs, T(hi, "toote formula theek kiye", "broken formulas fixed")],
          [c.text_to_number, T(hi, "text wale numbers asli number banaye", "text-numbers turned into numbers")],
          [c.trim_spaces, T(hi, "cells se extra space hataye", "cells cleaned of extra spaces")],
          [c.remove_duplicates, T(hi, "duplicate rows hatayi", "duplicate rows removed")],
        ].filter(([n]) => n);
        parts.push(el("p", "cap", T(hi, "Ho gaya", "Done")));
        const ul = el("ul", "findings done");
        for (const [n, t] of lines) ul.append(el("li", null, `${n.toLocaleString("en-IN")} ${t}`));
        if (!lines.length) ul.append(el("li", null, T(hi, "Kuch badalne ki zaroorat nahi padi.", "Nothing needed changing.")));
        parts.push(ul);
        if (c.broken_refs_left) parts.push(el("p", "note", T(hi, `${c.broken_refs_left} toote formula main khud nahi bana saka (column mein koi saaf pattern nahi). Inhe "Changes" sheet mein dekho.`, `${c.broken_refs_left} broken formulas had no clear pattern to rebuild from; see the "Changes" sheet.`)));
        if (c.duplicates_skipped_sheets) parts.push(el("p", "note", T(hi, "Jin sheets mein formula hain wahan duplicate rows nahi hatayi, taaki formula na toote.", "Duplicates weren't removed on sheets with formulas, so no formula breaks.")));
        if (!r.formatting_kept) parts.push(el("p", "note", T(hi, "Badi file thi, isliye values aur formula rakhe hain, rang/format nahi.", "Big file: values and formulas are kept, colours and formatting aren't.")));
        parts.push(el("p", "fine small", T(hi, "Har badlav fixed file ki \"Changes by Goal Engine\" sheet mein likha hai.", "Every change is listed in the fixed file's \"Changes by Goal Engine\" sheet.")));
      } else {
        parts.push(el("p", "cap", T(hi, `${f.name} vs ${r.other} · "${r.key}" column se milaya`, `${f.name} vs ${r.other} · matched on "${r.key}"`)));
        const ul = el("ul", "findings done");
        ul.append(el("li", null, `${(r.matched || 0).toLocaleString("en-IN")} ${T(hi, "rows dono mein", "rows in both")}`),
                  el("li", null, `${(r.only_in_a || 0).toLocaleString("en-IN")} ${T(hi, "sirf pehli file mein", "only in the first file")}`),
                  el("li", null, `${(r.only_in_b || 0).toLocaleString("en-IN")} ${T(hi, "sirf doosri file mein", "only in the second file")}`),
                  el("li", null, `${(r.changed_rows || 0).toLocaleString("en-IN")} ${T(hi, "rows mein value badli", "rows with changed values")} (${(r.changed_cells || 0).toLocaleString("en-IN")} cells)`));
        parts.push(ul, el("p", "fine small", T(hi, "Extra space, chhote-bade akshar aur text wale numbers ko farak nahi maana.", "Extra spaces, capital/small letters and numbers saved as text weren't counted as differences.")));
      }
      if (f.download) {
        const dl = el("a", "btn solid", T(hi, "Download karo", "Download")); dl.href = `/api/office/files/${f.id}/download`;
        parts.push(dl);
      }
      out.replaceChildren(...parts);
    };

    box.append(drop, out);
    const last = (a.files || [])[0];
    if (last && (last.status === "ready" || last.status === "error")) showFile(last);
    else if (last && last.status !== "uploading") { status(T(hi, "Kaam chal raha hai…", "Working…")); officeWait(last.id).then(showFile).catch(() => {}); }
    return box;
  }

  function renderOffice(a) {
    const hi = a.lang !== "en";
    const card = el("article", "agent office-agent");
    const head = el("div", "agent-name");
    head.append(el("h2", null, a.name), el("span", "state proposed", T(hi, "Taiyar", "Ready")));
    card.append(head);
    if (a.brain && a.brain.text) card.append(el("p", "brain-seen", (hi ? "Samjha: " : "Understood: ") + a.brain.text));
    if (a.formula) {
      const fx = el("div", "formula");
      fx.append(el("p", "cap", a.formula.title), el("code", null, a.formula.excel), el("p", "fine", a.formula.explain));
      const copy = el("button", "btn", T(hi, "Copy", "Copy")); copy.type = "button";
      copy.addEventListener("click", () => { navigator.clipboard && navigator.clipboard.writeText(a.formula.excel); copy.textContent = T(hi, "Copy ho gaya", "Copied"); });
      fx.append(copy);
      card.append(fx);
    }
    card.append(keep(`office:${a.id}`, () => officePanel(a, hi)));
    return card;
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
    if (c.kind === "php" && !c.online) { card.append(phpPanel(a, hi, c)); return card; }

    const out = el("div", "db-out");
    const say = (text) => { out.replaceChildren(el("p", "waiting", text)); };
    const fail = (err) => { out.replaceChildren(el("p", "note err", err.message)); };
    const meta = el("p", "access connector", `${c.name}${c.db_name && c.db_name !== c.name ? ` · ${c.db_name}` : ""}${c.db_type ? ` (${c.db_type})` : ""} · ${c.allow_changes ? T(hi, "padhna + approved changes (Undo ke saath)", "read + approved changes (with Undo)") : T(hi, "sirf read-only", "read-only")}`);
    card.append(meta);
    if (c.can_write && !c.allow_changes) card.append(el("p", "note warn", T(hi, "Is database user ke paas data badalne ka haq hai. Goal Engine kabhi kuch badalta nahi, phir bhi sirf-SELECT wala user behtar hai.",
                                                       "This database user can change data. Goal Engine never changes anything, but a SELECT-only user is safer.")));
    if (!c.online && c.kind !== "direct") {
      card.append(el("p", "note", T(hi, "Connector band hai. Server pe chalao: cd ~/goal-connector && .venv/bin/python goal_connector.py run &", "The connector isn't running. On your server: cd ~/goal-connector && .venv/bin/python goal_connector.py run &")));
      watchConnector(c.id, a.id);
    }

    // Tabs: tables and tools / what the database is doing now / what Goal Engine did
    const tabs = el("div", "tabs");
    tabs.setAttribute("role", "tablist");
    const pane = el("div", "pane");
    const livePane = el("div", "pane");
    const actPane = el("div", "pane");
    const views = { tables: pane, live: livePane, activity: actPane };
    const labels = { tables: T(hi, "Tables", "Tables"), live: T(hi, "Live", "Live"), activity: T(hi, "Activity", "Activity") };
    const show = (key) => {
      stopWatchers();
      for (const [k, el2] of Object.entries(views)) el2.hidden = k !== key;
      for (const b2 of tabs.children) b2.setAttribute("aria-selected", String(b2.dataset.key === key));
      dbTab[c.id] = key;
      if (key === "live") startLive(c, hi, livePane);
      if (key === "activity") startActivity(c, hi, actPane);
    };
    for (const key of Object.keys(views)) {
      const b2 = el("button", "tab", labels[key]); b2.type = "button"; b2.dataset.key = key; b2.setAttribute("role", "tab");
      b2.addEventListener("click", () => show(key));
      tabs.append(b2);
    }
    card.append(tabs, pane, livePane, actPane);
    if (c.online) pane.append(keep(`brain:${a.id}:${c.id}`, () => brainPanel(c, a, hi)));
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
    pane.append(tables);

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
    if (a.mode === "explore" || a.mode === "export") pane.append(sql);

    const acts = el("div", "acts");
    const hb = el("button", a.mode === "health" ? "btn solid" : "btn", T(hi, "Health check", "Health check")); hb.type = "button";
    hb.addEventListener("click", async () => {
      say(T(hi, "Database check kar raha hoon…", "Checking the database…"));
      try { out.replaceChildren(healthView((await runJob(c.id, { kind: "health" })).result, hi)); } catch (err) { fail(err); }
    });
    acts.append(hb);
    if (a.mode !== "explore" && a.mode !== "export") acts.append(el("span"));
    pane.append(acts);
    if (a.mode === "health" || a.mode === "report") pane.append(sql);
    pane.append(keep(`changes:${a.id}:${c.id}:${c.allow_changes ? 1 : 0}`, () => changesBox(c, a, hi)));

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
      pane.append(rep);
      const ga = el("div", "acts"); ga.append(go); card.append(ga);
      pane.append(ownerBox(a, w));
    }
    pane.append(out);

    queueMicrotask(() => show(dbTab[c.id] || "tables"));

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
    stopWatchers();
    const box = $("#agents");
    box.replaceChildren();
    for (const a of agents || []) {
      box.append(a.automation ? renderAutomation(a) : a.database ? renderDatabase(a) : a.office ? renderOffice(a) : a.db_report ? renderDbReport(a) : renderCheckAgent(a));
    }
    if (agents && agents.length && currentGoalId) {
      const hi = agents[0].lang !== "en";
      const link = el("button", "linkish expert-link", T(hi, "Isme madad chahiye? Expert se baat karo", "Need a hand with this? Talk to an expert"));
      link.type = "button";
      link.addEventListener("click", () => {
        const goal = { id: currentGoalId, title: (agents[0].name || "") };
        const holder = el("div", "turn-machine");
        holder.append(expertForm(goal, "", hi));
        thread.append(holder);
        holder.scrollIntoView({ block: "end", behavior: reduceMotion ? "auto" : "smooth" });
      });
      box.append(link);
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
  // ---------------------------------------------------------------- a human expert, when no agent can do it
  const expertAsked = new Set();

  function expertOffer(goal, lastText, hi, always) {
    const box = el("div", "expert");
    if (expertAsked.has(goal.id) && !always) return box;
    const q = el("p", "expert-q", always ? T(hi, "Kisi expert se baat karni hai?", "Want to talk to an expert?")
                                         : T(hi, "Is kaam ke liye abhi agent nahi hai. Kisi expert se baat karni hai?", "There's no agent for this yet. Want to talk to an expert?"));
    const yes = el("button", "btn solid", T(hi, "Haan, baat karni hai", "Yes, talk to an expert")); yes.type = "button";
    const no = el("button", "btn", T(hi, "Abhi nahi", "Not now")); no.type = "button";
    const row = el("div", "acts flat"); row.append(yes, no);
    box.append(q, row);
    no.addEventListener("click", () => box.remove());
    yes.addEventListener("click", () => box.replaceChildren(expertForm(goal, lastText, hi)));
    return box;
  }

  function expertForm(goal, lastText, hi) {
    const form = el("form", "expert-form");
    const field = (label, input) => { const l = el("label", "f"); l.append(el("span", null, label), input); return l; };
    const need = el("textarea"); need.value = lastText || goal.title || ""; need.maxLength = 2000; need.required = true;
    const name = el("input"); name.autocomplete = "name"; name.required = true; name.maxLength = 80;
    const phone = el("input"); phone.type = "tel"; phone.autocomplete = "tel"; phone.placeholder = "98xxxxxxxx";
    const email = el("input"); email.type = "email"; email.autocomplete = "email"; email.placeholder = "aap@email.com";
    const how = el("div", "how");
    let contact = "whatsapp";
    const opts = [["whatsapp", "WhatsApp"], ["call", T(hi, "Call", "Call")], ["email", "Email"]];
    for (const [v, label] of opts) {
      const b = el("button", "pill", label); b.type = "button"; b.setAttribute("aria-pressed", String(v === contact));
      b.addEventListener("click", () => {
        contact = v;
        for (const x of how.children) x.setAttribute("aria-pressed", String(x === b));
        phoneField.hidden = v === "email"; emailField.hidden = v !== "email";
      });
      how.append(b);
    }
    const phoneField = field(T(hi, "Mobile number", "Mobile number"), phone);
    const emailField = field("Email", email); emailField.hidden = true;
    const trap = el("input"); trap.name = "website"; trap.tabIndex = -1; trap.autocomplete = "off"; trap.className = "trap"; trap.setAttribute("aria-hidden", "true");
    const consentRow = el("label", "check");
    const consent = el("input"); consent.type = "checkbox";
    consentRow.append(consent, el("span", null, T(hi, "Chetan Sharma (Goal Engine) mujhse is baare mein contact kar sakte hain. Meri details sirf isi ke liye use hongi.",
                                                    "Chetan Sharma (Goal Engine) may contact me about this. My details are used only for this.")));
    const go = el("button", "btn go", T(hi, "Request bhejo", "Send request")); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true;
    form.append(field(T(hi, "Aapko kya chahiye?", "What do you need?"), need), field(T(hi, "Aapka naam", "Your name"), name),
                el("span", "lbl", T(hi, "Kaise baat karein?", "How should we reach you?")), how, phoneField, emailField, trap, consentRow, go, err);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      go.disabled = true; err.hidden = true;
      try {
        await api("/api/expert", { method: "POST", body: JSON.stringify({ goal_id: goal.id, need: need.value, name: name.value,
          phone: phone.value, email: email.value, contact, consent: consent.checked, website: trap.value }) });
        expertAsked.add(goal.id);
        form.replaceChildren(el("p", "expert-done", T(hi, `Request bhej di. Chetan aapse ${contact === "email" ? "email" : contact === "call" ? "call" : "WhatsApp"} pe jaldi baat karenge, aam taur pe kuch ghanton mein.`,
                                                      `Request sent. Chetan will reach you on ${contact === "email" ? "email" : contact === "call" ? "a call" : "WhatsApp"} soon, usually within a few hours.`)));
      } catch (ex) { err.textContent = ex.message; err.hidden = false; go.disabled = false; }
    });
    return form;
  }

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
      const box = addMachine(data.reply, traceFor(data));
      if (data.offer_expert && data.goal) box.append(expertOffer(data.goal, text, data.goal.detected_language !== "English"));
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

  // ---------------------------------------------------------------- live status strip on the home screen
  async function refreshStatus() {
    const box = $("#status");
    if (body.dataset.state !== "idle" || document.hidden) return;
    let d;
    try { d = await api("/api/status"); } catch (_) { box.hidden = true; return; }
    const line = el("p", "st-line");
    line.append(el("span", "dot on"), el("span", null, d.online ? T(lastLang !== "en", "Sabhi agents online", "All agents online")
                                                                 : T(lastLang !== "en", "Kuch agents abhi uplabdh nahi", "Some agents are unavailable")));
    box.replaceChildren(line);
    box.hidden = false;
  }
  refreshStatus();
  setInterval(refreshStatus, 30000);
  document.addEventListener("visibilitychange", refreshStatus);

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
