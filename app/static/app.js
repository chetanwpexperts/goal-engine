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
  let lastLang = "en";                      // English unless the person writes Hinglish or Hindi

  const RUN_WORDS = {
    hi: { preview: "Preview dekho", start: "Start", pause: "Roko", again: "Phir se chalao", checking: "Chal raha hai…",
          paste: "Ya rows paste karo (CSV)", template: "Jo message jayega", none: "Aaj kisi ko message nahi jayega.",
          skipped: "bina email, chhode", cols: "Sheet mein ye columns chahiye", owner: "Start se pehle apna email verify karo. Customers ke replies isi pe aayenge.",
          ownerEmail: "aapka@email.com", business: "Business ka naam", sendCode: "Code bhejo", code: "6-digit code", verify: "Verify",
          verified: "Verified", next: "Agla run", noAccess: "Koi password ya access nahi chahiye", connector: "Aapke server pe read-only connector chahiye",
          soon: "Ye agent abhi ban raha hai. Aapki zaroorat note kar li hai.", copy: "Copy", copied: "Copied",
          rewritten: "Rewritten query", indexes: "Index commands", mapping: "Columns samjhe",
          check: "Pehle ye chalao: kaunse index pehle se hain", maybe: "Index commands (sirf wo lagao jo check query mein nahi dikhe)",
          state: { proposed: "Taiyar", planned: "Jald aa raha", running: "Chal raha", done: "Ho gaya", failed: "Ruk gaya", active: "Chal raha hai", paused: "Ruka hua" } },
    en: { preview: "Preview", start: "Start", pause: "Pause", again: "Run again", checking: "Working…",
          paste: "Or paste the rows (CSV)", template: "The message", none: "Nobody would get a message today.",
          skipped: "without email, skipped", cols: "Your sheet needs these columns", owner: "Verify your email before Start. Customer replies come here.",
          ownerEmail: "you@business.com", business: "Business name", sendCode: "Send code", code: "6-digit code", verify: "Verify",
          verified: "Verified", next: "Next run", noAccess: "No password or access needed", connector: "Needs a read-only connector on your server",
          soon: "This agent is being built. Your need is noted.", copy: "Copy", copied: "Copied",
          rewritten: "Rewritten query", indexes: "Index commands", mapping: "Columns understood",
          check: "Run this first: which indexes you already have", maybe: "Index commands (add only the ones the check doesn't show)",
          state: { proposed: "Ready", planned: "Coming soon", running: "Running", done: "Done", failed: "Stopped", active: "Running", paused: "Paused" } },
  };
  const W = (lang) => RUN_WORDS[lang === "en" ? "en" : "hi"];

  async function api(path, options = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...options });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) {
      if (data.db_area) document.dispatchEvent(new CustomEvent("ge:dbarea", { detail: data.db_area }));
      const e = new Error(data.error || data.detail || "Something went wrong. Try again."); e.data = data; throw e;
    }
    return data;
  }

  // ---------------------------------------------------------------- composer
  const EXAMPLES = [
    "Tell it what's wrong with your Excel file or database…",
    "My Excel total is wrong",
    "VLOOKUP shows #N/A for some rows",
    "ERROR 1062 Duplicate entry when saving the form",
    "Net Sales 1 lakh se zyada wale rows green karo",
    "Deadlock found when trying to get lock, why?",
    "Find duplicate entries in my sheet",
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
    placeBelow();
  }

  // On the home page, the status line and the brain tiles always sit just under the input, however tall it grows.
  function placeBelow() {
    const r = document.getElementById("composer").getBoundingClientRect();
    document.documentElement.style.setProperty("--below", `${Math.round(r.bottom)}px`);
  }
  window.addEventListener("resize", placeBelow);
  requestAnimationFrame(placeBelow);

  function updateWordcount() {
    if (!wordTarget) { wordcount.hidden = true; return; }
    const n = (input.value.trim().match(/\S+/g) || []).length;
    wordcount.hidden = false;
    wordcount.textContent = n >= wordTarget ? `${n} words` : `${n} / ${wordTarget} words`;
    wordcount.classList.toggle("ok", n >= wordTarget);
  }

  // Home: the input box sits inside the centred column; in a conversation it docks at the bottom.
  const composerEl = document.getElementById("composer");
  const slot = document.getElementById("composer-slot");
  if (slot && body.dataset.state === "idle") slot.appendChild(composerEl);

  function setActive() {
    try { localStorage.setItem("ge_chat", "1"); } catch (_) { /* private mode: just no early chat view */ }
    if (body.dataset.state === "active" && composerEl.parentNode !== slot) return;
    body.dataset.state = "active";
    document.body.insertBefore(composerEl, document.getElementById("options"));
    clearTimeout(typer);
    typeExamples();
  }
  function setIdle() {
    try { localStorage.removeItem("ge_chat"); } catch (_) { /* nothing to clear */ }
    if (body.dataset.state === "idle") return;
    body.dataset.state = "idle";
    if (slot) slot.appendChild(composerEl);
    clearTimeout(typer);
    typeExamples();
  }
  // opened straight into the chat view (the page remembered a conversation): dock the input box there too
  if (body.dataset.state === "active") setActive();

  // the likely next questions, written with the person's own sheet and column names; one tap asks it
  function nextChips(items, hi) {
    const wrap = el("div", "next-asks");
    wrap.append(el("span", "next-label", hi ? "Ye bhi pooch sakte ho" : "Related questions"));
    const row = el("div", "next-row");
    for (const q of items) {
      const b = el("button", "next-chip", q);
      b.type = "button";
      b.addEventListener("click", () => { wrap.remove(); sendMessage(q); });
      row.append(b);
    }
    wrap.append(row);
    return wrap;
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
    box.append(richSay(text));
    const ans = /"(Answer \d+ - [^"]{1,30})"/.exec(text || "");
    if (ans && currentGoalId) {
      // every answer that made a sheet keeps a View button, even after newer answers replace the card
      const gid = currentGoalId;
      const view = el("button", "btn ans-view", /mein jod di|Download karo/.test(text) ? "Dekho" : "View"); view.type = "button";
      view.addEventListener("click", async () => {
        view.disabled = true;
        try {
          const d = await api(`/api/office/answer?goal_id=${gid}&name=${encodeURIComponent(ans[1])}`);
          const rows = d.rows.map((r) => r.map((v) => (typeof v === "number" ? v.toLocaleString("en-IN") : v)));
          const nodes = [el("h2", "acc-h", d.name), grid({ columns: d.columns, rows })];
          if (d.more) nodes.push(el("p", "fine", "First 50 rows. Download the file from the card for all of them."));
          openDrawer(nodes);
        } catch (ex) { addMachine(ex.message, [], true); }
        view.disabled = false;
      });
      box.append(view);
    }
    if (!window.GE_me && /Create a free account|free account banao/.test(text || "")) {
      const cta = el("button", "btn solid acc-cta", /banao/.test(text) ? "Free account banao" : "Create free account");
      cta.type = "button"; cta.dataset.account = "signup";
      box.append(cta);
    }
    thread.append(box);
    box.scrollIntoView({ block: "end", behavior: reduceMotion ? "auto" : "smooth" });
    return box;
  }

  // A reply in readable parts: paragraphs, bullet lists, and code (queries, formulas) in a box with Copy.
  const CODE_START = /^\s*(--\s|select|with|update|delete|insert|alter|create|drop|show|explain|set|begin|commit|grant|=[A-Z]|\$|<\?php|function |def |SELECT)/i;
  function richSay(text) {
    const wrap = el("div", "say");
    const blocks = String(text || "").split(/\n\s*\n/);
    let code = [];
    const flush = () => {
      if (!code.length) return;
      wrap.append(codeBox("", code.join("\n\n"), W(lastLang === "en" ? "en" : "hi")));
      code = [];
    };
    for (const b of blocks) {
      const lines = b.split("\n");
      if (CODE_START.test(lines[0]) && (lines.length > 1 || /[;()=]/.test(b))) { code.push(b.replace(/\s+$/, "")); continue; }
      flush();
      if (lines.every((l) => /^\s*[-•]\s+/.test(l) || !l.trim())) {
        const ul = el("ul", "say-list");
        for (const l of lines) if (l.trim()) ul.append(el("li", null, l.replace(/^\s*[-•]\s+/, "")));
        wrap.append(ul);
      } else if (lines.length > 1 && lines.slice(1).every((l) => /^\s*[-•]\s+/.test(l))) {
        wrap.append(el("p", null, lines[0]));
        const ul = el("ul", "say-list");
        for (const l of lines.slice(1)) ul.append(el("li", null, l.replace(/^\s*[-•]\s+/, "")));
        wrap.append(ul);
      } else {
        wrap.append(el("p", null, b));
      }
    }
    flush();
    return wrap;
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
    if (r.check_query) wrap.append(codeBox(w.check, r.check_query, w));
    if (r.indexes && r.indexes.length) wrap.append(codeBox(r.indexes_verified === false ? w.maybe : w.indexes, r.indexes.join("\n"), w));
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

  function formPanel(a, hi, onUseCommand, opts = {}) {
    const form = el("form", "dbform inner");
    const field = (label, input) => { const l = el("label", "f"); l.append(el("span", null, label), input); return l; };
    const type = el("select");
    for (const [v, t] of [["mysql", "MySQL / MariaDB"], ["postgres", "PostgreSQL"]]) { const o = el("option", null, t); o.value = v; type.append(o); }
    const host = el("input"); host.autocomplete = "off"; host.required = true;
    host.placeholder = opts.hosting ? T(hi, "aapki website, jaise mysite.com", "your website, e.g. mysite.com") : T(hi, "jaise db.provider.com", "e.g. db.provider.com");
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
    const allowStep = el("p", "fine strong-hint");
    if (opts.hosting) {
      allowStep.textContent = T(hi, "Ek baar: apni hosting ke cPanel → Remote MySQL (Hostinger: hPanel → Databases → Remote MySQL) mein Goal Engine ka IP add karo. Ye Goal Engine ke server ka address hai, sabke liye same; isse aapki hosting Goal Engine ko andar aane deti hai. Phir neeche wahi details bharo jo aapki website ki config mein hain; host mein localhost ki jagah apni website ka naam.",
        "Once: in your hosting's cPanel → Remote MySQL (Hostinger: hPanel → Databases → Remote MySQL), add Goal Engine's IP. It is Goal Engine's server address, the same for everyone; it lets your hosting accept Goal Engine. Then fill in the same details your website's config has; for host, your website's name instead of localhost.");
      form.prepend(allowStep);
    }
    api("/api/connector-info").then((d) => {
      if (!d.server_ip) return;
      if (opts.hosting) allowStep.textContent = allowStep.textContent.replace(T(hi, "Goal Engine ka IP", "Goal Engine's IP"), T(hi, `Goal Engine ka IP ${d.server_ip}`, `Goal Engine's IP ${d.server_ip}`));
      else ipHint.textContent += T(hi, ` Provider ke "allowed IPs" mein ye daalo: ${d.server_ip}`, ` Add this to your provider's allowed IPs: ${d.server_ip}`);
    }).catch(() => {});
    const go = el("button", "btn solid", T(hi, "Jodo", "Connect")); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true;
    const ga = el("div", "acts flat"); ga.append(go);
    const other = el("button", "btn ghost", T(hi, "Doosra tareeka: website pe ek file upload karo", "Another way: upload a file to the website")); other.type = "button";
    other.hidden = true;
    if (opts.other) other.addEventListener("click", opts.other);
    form.append(ipHint, ga, err, other);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (opts.hosting && /^(localhost|127\.0\.0\.1|::1)$/i.test(host.value.trim())) {
        err.textContent = T(hi, "Host mein localhost nahi: apni website ka naam likho (jaise mysite.com). localhost sirf hosting ke andar kaam karta hai.",
                               "Not localhost here: type your website's name (e.g. mysite.com). localhost only works inside the hosting.");
        err.hidden = false; return;
      }
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
        if (opts.other) other.hidden = false;        // the hosting won't let us in from outside: offer the other way, never a dead end
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

  // ---- the locked database area: account, confirmed email, fresh code; locks after 15 idle minutes
  function setupBox(a, hi) {
    const box = el("div", "slot");
    box.append(el("p", "fine", T(hi, "Check kar raha hoon…", "Checking…")));
    (window.GE_meReady || Promise.resolve()).then(() => dbGate(a, hi, box));
    return box;
  }

  async function dbGate(a, hi, box) {
    const me = window.GE_me;
    const btn = (label, cls, fn) => { const b = el("button", cls, label); b.type = "button"; b.addEventListener("click", fn); return b; };
    if (window.GE_dbSoon) { box.replaceChildren(el("p", "ask", T(hi, "Database jodna kuch dinon mein shuru hoga", "Connecting your database opens in a few days")),
      el("p", "fine", T(hi, "Tab tak error paste karo: wajah aur fix turant milega.", "Until then, paste the error and get the cause and the fix right away."))); return; }
    if (!me) {
      const row = el("div", "acts flat");
      row.append(btn(T(hi, "Free account banao", "Create free account"), "btn solid", () => window.GE_openAccount("signup")),
                 btn(T(hi, "Log in", "Log in"), "btn", () => window.GE_openAccount("login")));
      box.replaceChildren(
        el("p", "ask", T(hi, "Database jodne ke liye free account chahiye", "Connecting a database needs a free account")),
        el("p", "fine", T(hi, "Taaki sirf aap hi apna database dekh sako. Password sirf kaam ke dauraan rakha jaata hai, phir delete.",
                              "So only you can reach your database. Your password is kept only while you're working, then deleted.")), row);
      return;
    }
    if (!me.email_verified) {
      box.replaceChildren(el("p", "ask", T(hi, "Pehle apna email confirm karo", "Confirm your email first")),
                          btn(T(hi, "Email confirm karo", "Confirm email"), "btn solid", () => window.GE_openAccount("verify")));
      return;
    }
    let st;
    try { st = await api("/api/db-area"); } catch (ex) { box.replaceChildren(el("p", "note err", ex.message)); return; }
    if (st.state === "soon") { window.GE_dbSoon = true; return dbGate(a, hi, box); }
    if (st.state !== "open") { box.replaceChildren(...dbUnlock(hi, () => dbGate(a, hi, box))); return; }
    box.replaceChildren();
    setupBoxOpen(a, hi, box);
    const lockRow = el("p", "fine db-open");
    lockRow.append(el("span", null, T(hi, "Database area khula hai. 15 minute bina kaam ke apne aap lock ho jayega. ",
                                        "Database area open. It locks itself after 15 minutes without use. ")));
    const lockNow = el("button", "linkish inline", T(hi, "Abhi lock karo", "Lock now")); lockNow.type = "button";
    lockNow.addEventListener("click", async () => { await api("/api/db-area/lock", { method: "POST" }); dbGate(a, hi, box); });
    lockRow.append(lockNow);
    box.prepend(lockRow);
  }

  function dbUnlock(hi, onOpen) {
    const send = el("button", "btn solid", T(hi, "Code bhejo", "Email me a code")); send.type = "button";
    const note = el("p", "fine", T(hi, "Har baar database area kholne ke liye aapke email pe ek naya code aata hai. Isse koi aur aapke account se database nahi jod sakta.",
                                       "Each time you open the database area, we email you a fresh code. So nobody else can connect a database with your account."));
    const holder = el("div");
    send.addEventListener("click", async () => {
      send.disabled = true;
      try {
        const d = await api("/api/db-area/code", { method: "POST" });
        note.textContent = T(hi, `Code ${d.email} pe bheja. 10 minute chalega.`, `Code sent to ${d.email}. It works for 10 minutes.`);
        send.hidden = true;
        holder.replaceChildren(codeForm(async (code) => {
          await api("/api/db-area/open", { method: "POST", body: JSON.stringify({ code }) });
          onOpen();
        }, T(hi, "Kholo", "Open")));
      } catch (ex) { note.textContent = ex.message; send.disabled = false; }
    });
    return [el("p", "ask", T(hi, "Database area locked hai", "Your database area is locked")), note, send, holder];
  }
  window.GE_dbUnlock = dbUnlock;

  function setupBoxOpen(a, hi, box) {
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
      ["hosting", T(hi, "Website hosting", "Website hosting"), T(hi, "cPanel, Hostinger, koi bhi website · bas details bharo", "cPanel, Hostinger, any website · just fill the details")],
      ["cloud", T(hi, "Cloud database", "Cloud database"), T(hi, "AWS, DigitalOcean, Supabase, Neon · bas details bharo", "AWS, DigitalOcean, Supabase, Neon · just fill the details")],
      ["vps", T(hi, "Apna server / VPS", "My own server / VPS"), T(hi, "Terminal access hai · ek command", "You have a terminal · one command")],
      ["windows", T(hi, "Windows PC / XAMPP", "Windows PC / XAMPP"), T(hi, "Jald aa raha", "Coming soon")],
    ];
    const pick = (key) => {
      for (const b of choices.children) b.setAttribute("aria-pressed", String(b.dataset.key === key));
      panel.replaceChildren();
      if (key === "hosting") panel.append(formPanel(a, hi, null, { hosting: true, other: () => panel.replaceChildren(phpPanel(a, hi, null)) }));
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

  // ---- answer view: steps rail, overview, cause -> fix diagram, before/after and result charts (real data only)
  const PROBLEM = {
    broken_refs: ["Toote formula (#REF!)", "Broken formulas (#REF!)", "fix_broken_refs"],
    errors: ["Error wale cells", "Error cells", null],
    duplicate_rows: ["Duplicate rows", "Duplicate rows", "remove_duplicates"],
    text_numbers: ["Text wale numbers", "Numbers saved as text", "text_to_number"],
    spaces: ["Extra space", "Extra spaces", "trim_spaces"],
    case_mixed: ["Capital/small alag", "Mixed capitals", "fix_case"],
    odd_formulas: ["Alag formula wali rows", "Odd formulas", null],
  };
  const FIXNAME = { fix_broken_refs: ["Formula dobara banaye", "Formulas rebuilt"], remove_duplicates: ["Duplicate hataye", "Duplicates removed"],
    text_to_number: ["Asli number banaye", "Turned into numbers"], trim_spaces: ["Space hataye", "Spaces removed"], fix_case: ["Spelling ek jaisi", "Same spelling"] };
  const svgIcon = (d) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.innerHTML = d; return s; };
  const ICON_BRAIN = '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a3 3 0 0 0-3-1zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/>';

  function barChart(rows, title, line, highlight) {
    const box = el("div", "ans-chart");
    box.append(el("p", "ans-chart-title", title ? title.charAt(0).toUpperCase() + title.slice(1) : ""));
    const nums = rows.map((r) => Number(r[1]) || 0);
    const max = Math.max(1, ...nums.map(Math.abs));
    if (line && rows.length > 1) {
      const w = 320, h = 120, pad = 8;
      const pts = nums.map((v, i) => [pad + (i * (w - 2 * pad)) / (rows.length - 1), h - pad - (v / max) * (h - 2 * pad)]);
      const svg = svgIcon(`<polyline points="${pts.map((p) => p.join(",")).join(" ")}" fill="none" stroke="#5B4FD6" stroke-width="2.5" stroke-linejoin="round"/>` +
        pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="#F2A33A"/>`).join(""));
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.classList.add("ans-line");
      const labels = el("div", "ans-line-labels");
      labels.append(el("span", null, String(rows[0][0])), el("span", null, String(rows[rows.length - 1][0])));
      box.append(svg, labels);
      return box;
    }
    for (const [label, v] of rows.slice(0, 12)) {
      const row = el("div", highlight != null && String(label).toLowerCase() === String(highlight).toLowerCase() ? "ans-bar hl" : "ans-bar");
      const fill = el("span", "fill"); fill.style.width = `${Math.max(2, (Math.abs(Number(v) || 0) / max) * 100)}%`;
      const track = el("span", "track"); track.append(fill);
      row.append(el("span", "lbl", String(label)), track, el("span", "val", Number(v || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })));
      box.append(row);
    }
    return box;
  }

  function answerView(f, hi) {
    const r = f.result || {}, rep = f.report || { sheets: [], summary: {} }, now = rep.summary || {}, before = f.before || now;
    const fmtN = (n) => Number(n || 0).toLocaleString("en-IN");
    const total = Object.keys(PROBLEM).reduce((n, k) => n + (now[k] || 0), 0);
    const totalBefore = Object.keys(PROBLEM).reduce((n, k) => n + (before[k] || 0), 0);
    const steps = f.steps || [];
    const changed = steps.reduce((n, st) => n + Object.entries(st.changes || {}).filter(([op]) => FIXNAME[op]).reduce((m, [, v]) => m + v, 0), 0);

    // what really happened, in order; nothing shown as done unless it was
    const rail = el("ol", "ans-rail");
    const step = (text, state) => { const li = el("li", state); li.append(el("span", "dot"), el("span", null, text)); rail.append(li); };
    if (rep.kind === "docx") step(T(hi, `Document padha: ${fmtN(now.words)} shabd, ${fmtN(now.paragraphs)} paragraphs, ${fmtN(now.tables)} tables`, `Read the document: ${fmtN(now.words)} words, ${fmtN(now.paragraphs)} paragraphs, ${fmtN(now.tables)} tables`), "done");
    else step(T(hi, `File padhi: ${fmtN(now.sheets)} sheets, ${fmtN(now.rows)} rows`, `Read the file: ${fmtN(now.sheets)} sheets, ${fmtN(now.rows)} rows`), "done");
    if (r.task === "ask") step(T(hi, "Sawaal ka jawab nikala", "Answered your question"), "done");
    else step(totalBefore ? T(hi, `${fmtN(totalBefore)} galtiyan mili`, `Found ${fmtN(totalBefore)} problems`) : T(hi, "Koi galti nahi mili", "No problems found"), "done");
    if (r.task !== "ask" && totalBefore) step(changed ? T(hi, `${fmtN(changed)} cells/rows theek kiye`, `Fixed ${fmtN(changed)} cells/rows`) : T(hi, "Abhi kuch nahi badla", "Nothing changed yet"), changed ? "done" : "wait");
    if (f.status !== "ready") step(T(hi, "Kaam chal raha hai…", "Working…"), "now");

    const overview = el("div", "ans-overview");
    const ic = el("span", "ans-icon"); ic.append(svgIcon(ICON_BRAIN));
    let title = T(hi, "File ki jaanch", "File check");
    let text = total ? T(hi, `${fmtN(total)} galtiyan hain. Neeche asli cells aur unka theek kiya hua roop dekho.`, `${fmtN(total)} problems. Below are the actual cells and what each fix will make of them.`)
      : T(hi, "Is file mein koi galti nahi hai.", "No problems in this file.");
    if (rep.kind === "docx" && r.task !== "ask") {
      title = T(hi, "Document padh liya", "Document read");
      text = T(hi, `${fmtN(now.words)} shabd, lagbhag ${fmtN(now.pages)} pages, ${fmtN(now.headings)} headings, ${fmtN(now.tables)} tables. Kuch bhi poocho: summary, headings, koi shabd kahan aaya, kisi topic pe kya likha hai, tables ke total.`,
        `${fmtN(now.words)} words, about ${fmtN(now.pages)} pages, ${fmtN(now.headings)} headings, ${fmtN(now.tables)} tables. Ask anything: a summary, the headings, where a word appears, what it says about a topic, totals from its tables.`);
    }
    if (r.task === "ask") {
      title = r.value != null ? T(hi, "Jawab", "Answer") : (r.chart && r.chart.title) || T(hi, "Jawab", "Answer");
      title = title.charAt(0).toUpperCase() + title.slice(1);
      text = r.say;
    }
    else if (r.task === "fix") { title = T(hi, "Theek kar diya", "Fixed"); text = T(hi, `Pehle ${fmtN(totalBefore)} galtiyan thi, ab ${fmtN(total)}.`, `${fmtN(totalBefore)} problems before, ${fmtN(total)} now.`); }
    else if (r.task === "extract") { title = T(hi, "Duplicates ki list", "Duplicates listed"); text = T(hi, `${fmtN(r.count)} duplicate nayi sheet mein. Data nahi badla.`, `${fmtN(r.count)} duplicates listed in a new sheet. Your data wasn't changed.`); }
    else if (r.task === "compare") { title = T(hi, "Dono files ka milan", "Files compared"); text = T(hi, `${fmtN(r.only_in_a)} sirf pehli mein, ${fmtN(r.only_in_b)} sirf doosri mein, ${fmtN(r.changed_rows)} rows badli.`, `${fmtN(r.only_in_a)} only in the first, ${fmtN(r.only_in_b)} only in the second, ${fmtN(r.changed_rows)} rows changed.`); }
    const ot = el("div"); ot.append(el("h3", null, title), el("p", null, text));
    if (r.task === "ask" && r.estimate) ot.append(el("span", "ans-estimate", T(hi, "Andaza: file mein ye column nahi tha", "Estimate: worked out, not in the file")));
    overview.append(ic, ot);
    if (r.formula && (r.task !== "ask" || r.show_formula)) overview.append(el("code", "ans-formula", r.formula));

    const visual = el("div", "ans-visual");
    const exampleLine = (where, from, to) => {
      const li = el("li");
      li.append(el("span", "where", where), el("span", "from", JSON.stringify(String(from ?? ""))), el("span", "arrow", "→"),
                el("span", "to", to === "" || to == null ? T(hi, "(khud dekhna hoga)", "(needs a look)") : JSON.stringify(String(to))));
      return li;
    };

    if (r.task === "ask") {
      // the answer as a number, where it came from, how it compares, and the real rows behind it
      if (r.value != null) {
        const tile = el("div", "ans-number");
        const num = typeof r.value === "number" ? r.value.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : String(r.value);
        tile.append(el("b", null, num), el("span", null, `${r.value_label || ""}${r.where ? ` · ${r.where}` : ""}`));
        visual.append(tile);
      }
      if (r.chart && r.chart.rows && r.chart.rows.length > 1) visual.append(barChart(r.chart.rows, r.chart.title, r.chart.line, r.chart.highlight));
      if (r.sample && r.sample.rows && r.sample.rows.length) {
        const box = el("div", "ans-sample");
        box.append(el("p", "ans-chart-title", r.sample.total > r.sample.rows.length
          ? T(hi, `Gine gaye rows (pehle ${r.sample.rows.length}, kul ${fmtN(r.sample.total)})`, `Rows behind this answer (first ${r.sample.rows.length} of ${fmtN(r.sample.total)})`)
          : T(hi, "Gine gaye rows", "Rows behind this answer")));
        const wrap = el("div", "ans-table-wrap");
        const table = el("table", "ans-table");
        const hr = el("tr"); for (const h of r.sample.header) hr.append(el("th", null, String(h)));
        const thead = el("thead"); thead.append(hr); table.append(thead);
        const tb = el("tbody");
        for (const row of r.sample.rows) { const tr = el("tr"); for (const c of row) tr.append(el("td", null, typeof c === "number" ? c.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : String(c ?? ""))); tb.append(tr); }
        table.append(tb); wrap.append(table); box.append(wrap);
        visual.append(box);
      }
      if (!visual.childNodes.length) { const c = el("div", "ans-card"); c.append(el("p", null, r.say || "")); visual.append(c); }
    } else if (r.task === "fix" && r.examples && Object.keys(r.examples).length) {
      // what actually changed in the file, with the real values
      for (const [op, rows] of Object.entries(r.examples)) {
        if (!FIXNAME[op]) continue;
        const card = el("div", "ans-card good");
        card.append(el("h4", null, `${T(hi, ...FIXNAME[op])}: ${fmtN((r.changes || {})[op])}`));
        const ul = el("ul", "examples");
        for (const [sheet, cell, from, to] of rows) {
          if (op === "remove_duplicates") {
            const li = el("li"); li.append(el("span", "where", `${sheet} ${cell}`), el("span", "from", from || ""), el("span", null, T(hi, `hata di (${to || "duplicate"})`, `removed (${to || "duplicate"})`)));
            ul.append(li);
          } else ul.append(exampleLine(`${sheet}!${cell}`, from, to));
        }
        card.append(ul);
        visual.append(card);
      }
      const keys = Object.keys(PROBLEM).filter((k) => (before[k] || 0) !== (now[k] || 0));
      if (keys.length) {
        const ba = el("div", "ans-ba");
        const legend = el("p", "ans-chart-title");
        legend.append(document.createTextNode(T(hi, "Pehle vs ab ", "Before vs now ")), el("span", "lg before", T(hi, "pehle", "before")), el("span", "lg after", T(hi, "ab", "now")));
        ba.append(legend);
        const max = Math.max(1, ...keys.map((k) => before[k] || 0));
        for (const k of keys) {
          const row = el("div", "ba-row");
          const b1 = el("span", "b before"); b1.style.width = `${((before[k] || 0) / max) * 100}%`;
          const b2 = el("span", "b after"); b2.style.width = `${((now[k] || 0) / max) * 100}%`;
          const bars = el("span", "bars"); bars.append(b1, b2);
          row.append(el("span", "lbl", T(hi, PROBLEM[k][0], PROBLEM[k][1])), bars, el("span", "val", `${fmtN(before[k])} → ${fmtN(now[k])}`));
          ba.append(row);
        }
        visual.append(ba);
      }
    }
    // problems still in the file: the real cells, and exactly what the fix would do
    const open = Object.keys(PROBLEM).filter((k) => (now[k] || 0) > 0);
    if (open.length && r.task !== "ask") {
      const fixable = [];
      for (const k of open) {
        const [h, e, op] = PROBLEM[k];
        const card = el("div", `ans-card ${op ? "bad" : "info"}`);
        card.append(el("h4", null, `${T(hi, h, e)}: ${fmtN(now[k])}`));
        const ul = el("ul", "examples");
        for (const sh of rep.sheets || []) {
          if (k === "duplicate_rows") for (const [row, same, txt] of (sh.dup_samples || [])) {
            const li = el("li"); li.append(el("span", "where", `${sh.name} row ${row}`), el("span", null, T(hi, `row ${same} jaisi: ${txt}`, `same as row ${same}: ${txt}`))); ul.append(li);
          } else if (op && sh.samples && sh.samples[op]) for (const [cell, from, to] of sh.samples[op]) ul.append(exampleLine(`${sh.name}!${cell}`, from, to));
          else if (k === "errors") for (const [code, e2] of Object.entries(sh.errors || {})) { const li = el("li"); li.append(el("span", "where", sh.name), el("span", null, `${code}: ${e2.cells.slice(0, 5).join(", ")}`)); ul.append(li); }
          else if (k === "odd_formulas" && (sh.odd_formulas || []).length) { const li = el("li"); li.append(el("span", "where", sh.name), el("span", null, sh.odd_formulas.slice(0, 6).join(", "))); ul.append(li); }
        }
        card.append(ul);
        if (!op) card.append(el("p", "fine small", T(hi, "Ye main khud nahi badlunga; inhe ek baar dekh lo.", "I won't change these by myself; please take a look.")));
        else fixable.push(op);
        visual.append(card);
      }
      if (fixable.length) {
        const go = el("button", "btn solid", fixable.includes("remove_duplicates")
          ? T(hi, `Ye sab theek karo (${fmtN(now.duplicate_rows)} duplicate rows hatengi)`, `Fix all of these (removes ${fmtN(now.duplicate_rows)} duplicate rows)`)
          : T(hi, "Ye sab theek karo", "Fix all of these"));
        go.type = "button";
        go.addEventListener("click", () => { visual.dispatchEvent(new CustomEvent("fix", { detail: fixable, bubbles: true })); });
        visual.append(go);
      }
    } else if (!open.length && r.task !== "ask" && !(r.task === "fix")) {
      visual.append(el("p", "fine", T(hi, "Is file mein koi galti nahi hai. Ab koi bhi sawaal poocho: \"region wise total\", \"pending dikhao\"...",
                                        "No problems in this file. Ask anything: \"total by region\", \"show pending rows\"...")));
    }
    return { rail, overview, visual };
  }

  function officePanel(a, hi) {
    const box = el("section", "office");
    const out = el("div", "office-out");
    const pick = (label, multiple) => {
      const inp = el("input"); inp.type = "file"; inp.accept = ".xlsx,.xlsm,.xls,.xlsb,.ods,.csv,.txt,.docx,.doc"; inp.hidden = true; inp.multiple = !!multiple;
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
    const another = el("span", "another", T(hi, "Nayi file?", "Another file?"));
    drop.append(el("p", "lead", T(hi, "Excel (.xlsx) ya CSV file yahan chhodo. Excel 16 MB tak, CSV 26 MB tak.", "Drop an Excel (.xlsx) or CSV file here. Excel up to 16 MB, CSV up to 26 MB.")), another, btn, inp,
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
      ["case_mixed", "fix_case", true, (n) => T(hi, `${n} cells mein ek hi shabd alag capital/small letters mein (finance / Finance)`, `${n} cells spell the same word with different capitals (finance / Finance)`)],
      ["duplicate_rows", "remove_duplicates", false, (n) => T(hi, `${n} rows do baar hain (duplicate)`, `${n} repeated rows (duplicates)`)],
      ["errors", null, false, (n) => T(hi, `${n} cells mein error dikh raha hai (#N/A, #DIV/0! …)`, `${n} cells show an error (#N/A, #DIV/0! …)`)],
      ["odd_formulas", null, false, (n) => T(hi, `${n} rows ka formula baaki column se alag hai (galti ho sakti hai)`, `${n} rows have a formula different from the rest of the column (possible mistake)`)],
    ];

    const cellsOf = (rep, key) => {
      const list = [];
      for (const sh of rep.sheets) {
        const cells = key === "broken_refs" ? sh.broken_refs : key === "odd_formulas" ? sh.odd_formulas : key === "text_numbers" ? sh.text_number_cells
          : key === "spaces" ? sh.space_cells : key === "case_mixed" ? sh.case_cells : key === "errors" ? Object.values(sh.errors).flatMap((e) => e.cells) : [];
        for (const c of (cells || []).slice(0, 8)) list.push(rep.sheets.length > 1 ? `${sh.name}!${c}` : c);
      }
      return list.join(", ");
    };

    const fmt = (n) => Number(n || 0).toLocaleString("en-IN");
    const DONE = {
      fix_broken_refs: T(hi, "toote formula theek kiye", "broken formulas fixed"),
      text_to_number: T(hi, "text wale numbers asli number banaye", "text-numbers turned into numbers"),
      trim_spaces: T(hi, "cells se extra space hataye", "cells cleaned of extra spaces"),
      remove_duplicates: T(hi, "duplicate rows hatayi", "duplicate rows removed"),
      fix_case: T(hi, "cells mein capital/small letters ek jaise kiye", "cells made to use the same spelling"),
      list_duplicates: T(hi, "duplicate nayi sheet mein list kiye (data nahi badla)", "duplicates listed in a new sheet (data unchanged)"),
      answer: T(hi, "sawaal ka jawab nayi sheet mein", "answer added as a new sheet"),
    };
    let watching = "";

    // One file stays open: every task builds on the last one, until the person is done.
    const showFile = (f) => {
      if (f.status === "error") { out.replaceChildren(el("p", "note err", f.error)); return; }
      if (f.status !== "ready") {
        status(T(hi, "Kaam chal raha hai…", "Working…"));
        if (watching !== f.id) { watching = f.id; officeWait(f.id).then((g) => { watching = ""; showFile(g); }).catch(() => { watching = ""; }); }
        return;
      }
      box.classList.add("has-file");
      const rep = f.report, s = rep.summary, r = f.result || {};
      const parts = [el("p", "cap", rep.kind === "docx"
        ? `${f.name} · ${fmt(s.words)} ${T(hi, "shabd", "words")} · ${fmt(s.paragraphs)} ${T(hi, "paragraph", "paragraphs")} · ${fmt(s.tables)} ${T(hi, "table", s.tables === 1 ? "table" : "tables")}`
        : `${f.name} · ${s.sheets} ${T(hi, "sheet", s.sheets > 1 ? "sheets" : "sheet")} · ${fmt(s.rows)} ${T(hi, "rows", "rows")} · ${fmt(s.formulas)} ${T(hi, "formula", "formulas")}`)];

      if ((f.steps || []).length) {
        const ul = el("ul", "findings done");
        for (const st of f.steps) {
          if (st.say) { ul.append(el("li", null, st.say)); continue; }
          for (const [op, n] of Object.entries(st.changes || {})) if (DONE[op] && n) ul.append(el("li", null, `${fmt(n)} ${DONE[op]}`));
        }
        if (!ul.children.length) ul.append(el("li", null, T(hi, "Kuch badalne ki zaroorat nahi padi.", "Nothing needed changing.")));
        parts.push(el("p", "cap", T(hi, "Ab tak kiya", "Done so far")), ul);
        const c = r.task === "fix" ? (r.changes || {}) : {};
        if (c.broken_refs_left) parts.push(el("p", "note", T(hi, `${c.broken_refs_left} toote formula ka koi saaf pattern nahi mila; inhe khud dekhna padega.`, `${c.broken_refs_left} broken formulas had no clear pattern to rebuild from.`)));
        if (c.duplicates_skipped_sheets) parts.push(el("p", "note", T(hi, "Jin sheets mein formula hain wahan duplicate rows nahi hatayi, taaki formula na toote.", "Duplicates weren't removed on sheets with formulas, so no formula breaks.")));
        if (r.task === "fix" && r.formatting_kept === false) parts.push(el("p", "note", T(hi, "Badi file thi, isliye values aur formula rakhe hain, rang/format nahi.", "Big file: values and formulas are kept, colours and formatting aren't.")));
      }
      if (r.task === "extract") {
        parts.push(el("p", "cap", T(hi, `Duplicates nayi sheet mein: ${fmt(r.count)}`, `Duplicates in a new sheet: ${fmt(r.count)}`)));
        const ul = el("ul", "findings done");
        for (const g of r.groups || []) ul.append(el("li", null, g.what === "row"
          ? T(hi, `${g.sheet}: ${fmt(g.count)} rows poori ki poori do baar`, `${g.sheet}: ${fmt(g.count)} whole rows repeated`)
          : T(hi, `${g.sheet}: ${fmt(g.count)} baar same ${g.what}`, `${g.sheet}: same ${g.what} used ${fmt(g.count)} more times`)));
        if (!r.count) ul.append(el("li", null, T(hi, "Koi duplicate nahi mila.", "No duplicates found.")));
        parts.push(ul, el("p", "fine small", T(hi, "Aapka data nahi badla. \"Duplicates (Goal Engine)\" sheet file ki pehli sheet hai.", "Your data wasn't changed. The \"Duplicates (Goal Engine)\" sheet is the first sheet in the file.")));
      }
      if (r.task === "ask") {
        parts.push(el("p", "cap", T(hi, "Jawab", "Answer")), el("p", "answer", r.say));
        if (r.formula) {
          parts.push(el("p", "cap", T(hi, "Khud Excel mein karna ho to", "To do it yourself in Excel")));
          const fx = el("div", "formula");
          const code = el("code", null, r.formula);
          const copy = el("button", "btn", T(hi, "Formula copy karo", "Copy formula")); copy.type = "button";
          copy.addEventListener("click", () => { navigator.clipboard && navigator.clipboard.writeText(r.formula); copy.textContent = T(hi, "Copy ho gaya", "Copied"); });
          fx.append(code, copy);
          parts.push(fx);
        }
      }
      if (r.task === "undo") parts.push(el("p", "fine", T(hi, "Aakhri badlav wapas le liya.", "The last change was undone.")));
      if (r.task === "compare") {
        parts.push(el("p", "cap", T(hi, `${f.name} vs ${r.other} · "${r.key}" column se milaya`, `${f.name} vs ${r.other} · matched on "${r.key}"`)));
        const ul = el("ul", "findings done");
        ul.append(el("li", null, `${fmt(r.matched)} ${T(hi, "rows dono mein", "rows in both")}`),
                  el("li", null, `${fmt(r.only_in_a)} ${T(hi, "sirf pehli file mein", "only in the first file")}`),
                  el("li", null, `${fmt(r.only_in_b)} ${T(hi, "sirf doosri file mein", "only in the second file")}`),
                  el("li", null, `${fmt(r.changed_rows)} ${T(hi, "rows mein value badli", "rows with changed values")} (${fmt(r.changed_cells)} cells)`));
        parts.push(ul);
        if (f.compare_download) { const dl = el("a", "btn", T(hi, "Comparison report download", "Download the comparison")); dl.href = `/api/office/files/${f.id}/download?what=compare`; parts.push(dl); }
      }

      const ops = [];
      const ul = el("ul", "findings");
      for (const [key, op, on, text] of FIND) {
        const n = s[key] || 0;
        if (!n) continue;
        const li = el("li");
        if (op) {
          const cb = el("input"); cb.type = "checkbox"; cb.checked = on; cb.id = `of-${f.id}-${op}`;
          const lb = el("label", null, text(fmt(n))); lb.htmlFor = cb.id;
          li.append(cb, lb); ops.push([op, cb]);
        } else li.append(el("span", null, text(fmt(n))));
        const where = cellsOf(rep, key);
        if (where) li.append(el("small", null, `${T(hi, "Kahan", "Where")}: ${where}${n > 8 ? " …" : ""}`));
        ul.append(li);
      }
      parts.push(el("p", "cap", (f.steps || []).length ? T(hi, "Abhi bhi bacha hai", "Still in the file") : T(hi, "Jo maine dekha", "What I found")));
      parts.push(ul.children.length ? ul : el("p", "fine", T(hi, "Ab is file mein mujhe koi galti nahi dikhi.", "I see no problems left in this file.")));

      const acts = el("div", "acts");
      if (ops.length) {
        const fixBtn = el("button", "btn solid", T(hi, "Theek karo", "Fix it")); fixBtn.type = "button";
        fixBtn.addEventListener("click", async () => {
          const chosen = ops.filter(([, cb]) => cb.checked).map(([op]) => op);
          if (!chosen.length) return;
          try {
            showFile(await api(`/api/office/files/${f.id}/fix`, { method: "POST", body: JSON.stringify({ ops: chosen }) }));
          } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
        });
        acts.append(fixBtn);
      }
      if (f.download) {
        const dl = el("a", "btn solid", T(hi, "Download karo", "Download")); dl.href = `/api/office/files/${f.id}/download`;
        const undo = el("button", "btn", T(hi, "Aakhri badlav wapas lo", "Undo the last change")); undo.type = "button";
        undo.addEventListener("click", async () => {
          try { showFile(await api(`/api/office/files/${f.id}/undo`, { method: "POST" })); } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
        });
        acts.append(dl, undo);
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
          showFile(await api(`/api/office/files/${f.id}/compare`, { method: "POST", body: JSON.stringify({ other: g2.id }) }));
        } catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
      });
      acts.append(cmpBtn, cmpInp);
      parts.push(acts, el("p", "fine small", T(hi,
        "File khuli hai: agla kaam chat mein likho (jaise \"duplicate bhi hatao\") ya upar chuno. Har badlav file ki \"Changes by Goal Engine\" sheet mein likha hai. Kuch na karo to 1 ghante baad file apne aap delete.",
        "The file stays open: type the next task in the chat (like \"remove duplicates too\") or pick above. Every change is listed in the file's \"Changes by Goal Engine\" sheet. Left alone, it's deleted after an hour.")));
      // The answer, shown visually first; the full details sit in the second tab.
      const view = answerView(f, hi);
      view.visual.addEventListener("fix", async (ev) => {
        try { showFile(await api(`/api/office/files/${f.id}/fix`, { method: "POST", body: JSON.stringify({ ops: ev.detail }) })); }
        catch (ex) { out.replaceChildren(el("p", "note err", ex.message)); }
      });
      const details = el("div", "pane-details"); details.append(...parts);
      const tabs = el("div", "ans-tabs"); tabs.setAttribute("role", "tablist");
      const panes = [view.visual, details];
      const names = [T(hi, "Visual", "Visual"), T(hi, "Poori detail", "Details")];
      const btns = names.map((nm, i) => {
        const b = el("button", "ans-tab", nm); b.type = "button"; b.setAttribute("role", "tab");
        b.addEventListener("click", () => { btns.forEach((x, j) => x.setAttribute("aria-selected", String(i === j))); panes.forEach((pn, j) => { pn.hidden = i !== j; }); });
        return b;
      });
      btns[0].setAttribute("aria-selected", "true"); details.hidden = true;
      tabs.append(...btns);
      out.replaceChildren(view.rail, view.overview, tabs, ...panes);
    };

    box.append(drop, out);
    box._refresh = (a2) => {
      const latest = (a2.files || [])[0];
      if (latest && latest.status !== "uploading") showFile(latest);
    };
    box._refresh(a);
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
    const panel = keep(`office:${a.id}`, () => officePanel(a, hi));
    if (panel._refresh) panel._refresh(a);
    card.append(panel);
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
      if (data.next && data.next.length) box.append(nextChips(data.next, lastLang !== "en"));
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
  api("/api/known").then((k) => { const b = $("#stat-fixes"); if (b && k.fixes) b.textContent = Number(k.fixes).toLocaleString("en-IN"); }).catch(() => {});
  document.addEventListener("visibilitychange", refreshStatus);

  // ---------------------------------------------------------------- home: the brain tiles and the living network
  // What each area can do today and what is still coming: said plainly before anyone starts
  const SCOPE = {
    db: {
      title: ["Databases ke liye", "For databases"],
      now: [["MySQL, MariaDB aur PostgreSQL", "MySQL, MariaDB and PostgreSQL"],
            ["Error paste karo (jaise 1062 Duplicate entry, deadlock, access denied): wajah, fix aur aage se bachav", "Paste an error (e.g. 1062 Duplicate entry, deadlock, access denied): the cause, the fix and how to prevent it"],
            ["Slow query paste karo: kya galat hai, nayi query aur sahi index", "Paste a slow query: what's wrong, a rewritten query and the right indexes"],
            ["Free account ke saath: apna database jodo, agent khud check karke saboot ke saath jawab deta hai", "With a free account: connect your database and the agent checks it itself, with proof"]],
      soon: [["SQL Server, MongoDB, Redis, Oracle", "SQL Server, MongoDB, Redis, Oracle"]],
    },
    ms: {
      title: ["Microsoft tools ke liye", "For Microsoft tools"],
      now: [["Excel: problem batao, file mein asli wajah dhoondh ke saboot ke saath theek karta hai (Undo ke saath)", "Excel: tell it the problem; it finds the real cause in your file, shows the proof and fixes it (with Undo)"],
            ["Excel: badlav, charts, highlight, sawaal (total, region-wise, top 10…)", "Excel: changes, charts, highlighting, questions (totals, region-wise, top 10…)"],
            ["Word: replace, formatting, emails/phones/amounts nikalna, summary", "Word: replace, formatting, pulling out emails/phones/amounts, summary"]],
      soon: [["PowerPoint, Outlook", "PowerPoint, Outlook"], ["Macros, printing setup, sheet protection, nayi file banana", "Macros, print setup, sheet protection, creating new files"]],
    },
  };
  function showScope(tile) {
    const box = $("#scope");
    const sc = SCOPE[tile.dataset.scope];
    if (!box || !sc) return;
    const hi = lastLang !== "en";
    const pick = (pair) => pair[hi ? 0 : 1];
    const list = (items) => { const ul = el("ul"); for (const it of items) ul.append(el("li", null, pick(it))); return ul; };
    const tryIt = el("button", "btn solid", hi ? "Example try karo" : "Try an example"); tryIt.type = "button";
    tryIt.addEventListener("click", () => {
      input.value = tile.dataset.example;
      autosize();
      input.dispatchEvent(new Event("input"));
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
    // connecting a database is switched off at launch for most people: then it belongs under "coming soon"
    const connectItem = (it) => /connect your database/.test(it[1]);
    const now = window.GE_dbSoon ? sc.now.filter((it) => !connectItem(it)) : sc.now;
    const soon = window.GE_dbSoon ? [...sc.now.filter(connectItem).map(() => ["Apna database jodo, agent khud check karke saboot ke saath jawab deta hai",
                                                                              "Connect your database: the agent checks it itself, with proof"]), ...sc.soon] : sc.soon;
    box.replaceChildren(
      el("b", "scope-title", pick(sc.title)),
      el("p", "scope-h now", hi ? "Abhi kaam karta hai" : "Works now"), list(now),
      el("p", "scope-h soon", hi ? "Jald aa raha hai" : "Coming soon"), list(soon),
      tryIt);
    box.hidden = false;
    for (const t of document.querySelectorAll(".brain-tile")) t.setAttribute("aria-pressed", String(t === tile));
  }
  for (const tile of document.querySelectorAll(".brain-tile")) tile.addEventListener("click", () => showScope(tile));
  const syncTyping = () => body.classList.toggle("typing", !!input.value.trim() && !$("#options").hidden);
  input.addEventListener("input", syncTyping);

  // A brain drawn in code: a slowly turning 3D cloud of neurons. Signals fire along its connections,
  // faster while the person types. Light colours; still for reduced motion; paused when not visible.
  const neural = (() => {
    const cv = $("#brain");
    if (!cv || !cv.getContext) return { excite() {} };
    const ctx = cv.getContext("2d");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, pts = [], edges = [], adj = [], pulses = [], heat = 0, raf = 0, last = 0, t0 = performance.now(), sil = [], cereb = [], stem = [];
    const motes = Array.from({ length: 7 }, (_, i) => ({ a: i * 0.9 + Math.random(), s: 0.05 + Math.random() * 0.06, r: 0.38 + Math.random() * 0.1 }));

    // Side view of a brain (front to the right): the outline, the folds, the cerebellum and the stem.
    const OUTLINE = [[-0.92, -0.05], [-0.95, 0.15], [-0.86, 0.4], [-0.66, 0.6], [-0.38, 0.72], [-0.05, 0.77], [0.28, 0.71], [0.55, 0.56],
      [0.78, 0.34], [0.92, 0.09], [0.94, -0.12], [0.83, -0.28], [0.63, -0.33], [0.47, -0.27], [0.4, -0.33], [0.31, -0.48], [0.1, -0.58],
      [-0.15, -0.56], [-0.36, -0.46], [-0.56, -0.38], [-0.76, -0.28], [-0.88, -0.18]];
    const FOLDS = [
      [[0.43, -0.24], [0.2, -0.1], [-0.05, -0.02], [-0.3, 0.06]],                    // lateral fissure
      [[-0.06, 0.75], [0.0, 0.5], [0.08, 0.3], [0.14, 0.05], [0.2, -0.08]],          // central sulcus
      [[-0.25, 0.71], [-0.2, 0.45], [-0.12, 0.25], [-0.05, 0.08]],
      [[0.25, 0.7], [0.32, 0.45], [0.42, 0.25], [0.55, 0.1]],
      [[0.55, 0.55], [0.6, 0.35], [0.72, 0.18], [0.86, 0.05]],
      [[0.45, 0.62], [0.5, 0.42], [0.62, 0.3], [0.8, 0.25]],
      [[-0.6, 0.6], [-0.55, 0.35], [-0.6, 0.1], [-0.75, -0.05]],
      [[-0.45, 0.66], [-0.4, 0.4], [-0.45, 0.2], [-0.55, 0.0], [-0.7, -0.15]],
      [[0.35, -0.38], [0.1, -0.3], [-0.15, -0.3], [-0.4, -0.25]],                    // temporal folds
      [[0.25, -0.5], [0.0, -0.45], [-0.25, -0.42]],
      [[-0.85, 0.3], [-0.72, 0.25], [-0.62, 0.32]],
      [[0.7, -0.1], [0.55, -0.05], [0.4, 0.05], [0.3, 0.2]],
    ];
    function inside([x, y], poly) {
      let ok = false;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const [xi, yi] = poly[i], [xj, yj] = poly[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) ok = !ok;
      }
      return ok;
    }
    function edgeDist(x, y) {
      let best = 9;
      for (let i = 0, j = OUTLINE.length - 1; i < OUTLINE.length; j = i++) {
        const [ax, ay] = OUTLINE[j], [bx, by] = OUTLINE[i], dx = bx - ax, dy = by - ay;
        const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)));
        best = Math.min(best, Math.hypot(x - ax - t * dx, y - ay - t * dy));
      }
      return best;
    }
    const half = (x, y) => 0.62 * Math.sqrt(Math.min(1, edgeDist(x, y) / 0.42));   // how thick the hemisphere is here
    function shape(n) {
      const out = [], rnd = (a, b) => a + Math.random() * (b - a);
      let guard = 0;
      while (out.length < n * 0.5 && guard++ < n * 60) {        // the two surfaces
        const x = rnd(-1, 1), y = rnd(-0.65, 0.8);
        if (!inside([x, y], OUTLINE)) continue;
        out.push({ x, y, z: (Math.random() < 0.5 ? 1 : -1) * half(x, y), k: 0 });
      }
      for (let i = 0; i < OUTLINE.length; i++) {               // the silhouette
        const [ax, ay] = OUTLINE[i], [bx, by] = OUTLINE[(i + 1) % OUTLINE.length];
        for (let t = 0; t < 1; t += 0.2) out.push({ x: ax + (bx - ax) * t, y: ay + (by - ay) * t, z: rnd(-0.08, 0.08), k: 0 });
      }
      for (const f of FOLDS) {                                  // folds, drawn as rows of neurons on both sides
        for (let i = 0; i < f.length - 1; i++) {
          const [ax, ay] = f[i], [bx, by] = f[i + 1];
          for (let t = 0; t < 1; t += 0.12) {
            const x = ax + (bx - ax) * t + rnd(-0.01, 0.01), y = ay + (by - ay) * t + rnd(-0.01, 0.01);
            const h = half(x, y);
            out.push({ x, y, z: h * 1.02, k: 3 }, { x, y, z: -h * 1.02, k: 3 });
          }
        }
      }
      let c = 0;
      while (c < n * 0.12) {                                    // cerebellum
        const a = rnd(0, Math.PI * 2), r = Math.sqrt(Math.random());
        const x = -0.6 + 0.28 * r * Math.cos(a), y = -0.56 + 0.15 * r * Math.sin(a);
        if (inside([x, y], OUTLINE)) continue;
        out.push({ x, y, z: rnd(-0.42, 0.42) * Math.sqrt(1 - r * r), k: 1 }); c++;
      }
      for (let i = 0; i < n * 0.04; i++) {                      // brain stem
        const h = Math.random();
        out.push({ x: -0.2 - 0.1 * h + rnd(-0.05, 0.05), y: -0.55 - 0.38 * h, z: rnd(-0.07, 0.07), k: 2 });
      }
      return out;
    }

    function build() {
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      W = cv.clientWidth; H = cv.clientHeight;
      if (!W || !H) return;
      cv.width = W * dpr; cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      pts = shape(W < 360 ? 700 : 1500);
      edges = []; adj = pts.map(() => []);
      pts.forEach((p, i) => {
        const near = [];
        for (let j = 0; j < pts.length; j++) {
          if (j === i) continue;
          const q = pts[j], d = (p.x - q.x) ** 2 + (p.y - q.y) ** 2 + (p.z - q.z) ** 2;
          if (near.length < 3) { near.push([j, d]); near.sort((a, b) => a[1] - b[1]); }
          else if (d < near[2][1]) { near[2] = [j, d]; near.sort((a, b) => a[1] - b[1]); }
        }
        for (const [j, d] of near) if (d < 0.03 && i < j) { edges.push([i, j]); adj[i].push(j); adj[j].push(i); }
      });
      pts.forEach((p) => { p.glow = 0; });
      sil = OUTLINE.map(([x, y]) => ({ x, y, z: 0 }));
      cereb = Array.from({ length: 24 }, (_, i) => { const a = (i / 24) * Math.PI * 2; return { x: -0.5 + 0.27 * Math.cos(a), y: -0.47 + 0.15 * Math.sin(a), z: -0.05 }; });
      stem = [[-0.1, -0.47], [-0.04, -0.5], [-0.12, -0.9], [-0.19, -0.88], [-0.2, -0.6]].map(([x, y]) => ({ x, y, z: -0.02 }));
    }

    function project(time) {
      const a = Math.sin(time * 0.00028) * 0.42;             // turns a little left and right, always recognisable
      const tilt = 0.06 + Math.sin(time * 0.00019) * 0.05;
      const ca = Math.cos(a), sa = Math.sin(a), ct = Math.cos(tilt), st = Math.sin(tilt);
      const s = Math.min(W / 2.35, H / 2.05), cx = W / 2, cy = H * 0.42;
      for (const p of [...pts, ...sil, ...cereb, ...stem]) {
        const x1 = p.x * ca + p.z * sa, z1 = -p.x * sa + p.z * ca;
        const y1 = p.y * ct - z1 * st, z2 = p.y * st + z1 * ct;
        const f = 3.2 / (3.2 - z2);
        p.sx = cx + x1 * s * f; p.sy = cy - y1 * s * f; p.d = (z2 + 1.2) / 2.4;
      }
    }

    function fire() {
      if (!edges.length) return;
      const e = edges[Math.floor(Math.random() * edges.length)];
      const flip = Math.random() < 0.5;
      pulses.push({ a: flip ? e[1] : e[0], b: flip ? e[0] : e[1], t: 0, v: 1.4 + Math.random() * 1.6, hops: 4 + Math.floor(Math.random() * 6),
                    c: Math.random() < 0.7 ? "242, 163, 58" : "46, 170, 196" });
    }

    function draw(now) {
      const dt = Math.min(0.05, (now - last) / 1000 || 0.016); last = now;
      if (!W) build();
      ctx.clearRect(0, 0, W, H);
      project(still ? 0 : now - t0);
      const tt = (still ? 0 : now - t0) / 1000;
      const cx0 = W / 2, cy0 = H * 0.43, R = Math.min(W / 2.5, H / 2.1);
      // the light it gives off: a wide soft halo, a warm core, a violet rim
      const g = ctx.createRadialGradient(cx0, cy0, 0, cx0, cy0, R * 1.5);
      g.addColorStop(0, `rgba(255, 255, 255, ${0.9})`); g.addColorStop(0.25, `rgba(214, 220, 255, ${0.7 + heat * 0.2})`);
      g.addColorStop(0.6, "rgba(196, 190, 255, .25)"); g.addColorStop(1, "rgba(196, 206, 255, 0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      const core = ctx.createRadialGradient(cx0, cy0, 0, cx0, cy0, R * 0.9);
      core.addColorStop(0, `rgba(120, 130, 255, ${0.35 + heat * 0.1})`); core.addColorStop(1, "rgba(120, 90, 230, 0)");
      ctx.save(); ctx.translate(cx0, cy0); ctx.scale(1, 0.72); ctx.translate(-cx0, -cy0);
      ctx.fillStyle = core; ctx.beginPath(); ctx.arc(cx0, cy0, R * 0.9, 0, 7); ctx.fill(); ctx.restore();
      // two tilted orbits around it; the back half is drawn now, the front half after the brain
      const orbits = [[1.25, 0.26, -0.18, "242, 163, 58"], [1.12, 0.3, 0.22, "110, 120, 255"]];
      const ring = (front) => {
        for (const [rx, ry, rot, col] of orbits) {
          ctx.save(); ctx.translate(cx0, cy0 + R * 0.12); ctx.rotate(rot);
          const grd = ctx.createLinearGradient(-R * rx, 0, R * rx, 0);
          grd.addColorStop(0, `rgba(${col}, 0)`); grd.addColorStop(0.5, `rgba(${col}, ${front ? 0.85 : 0.35})`); grd.addColorStop(1, `rgba(${col}, 0)`);
          ctx.strokeStyle = grd; ctx.lineWidth = front ? 2 : 1.2;
          ctx.beginPath(); ctx.ellipse(0, 0, R * rx, R * ry, 0, front ? 0 : Math.PI, front ? Math.PI : Math.PI * 2); ctx.stroke();
          const a = (tt * 0.5 + rot * 3) % (Math.PI * 2), on = Math.sin(a) > 0;
          if (on === front) {
            const px = Math.cos(a) * R * rx, py = Math.sin(a) * R * ry;
            const dot = ctx.createRadialGradient(px, py, 0, px, py, 9);
            dot.addColorStop(0, `rgba(${col}, 1)`); dot.addColorStop(1, `rgba(${col}, 0)`);
            ctx.fillStyle = dot; ctx.beginPath(); ctx.arc(px, py, 9, 0, 7); ctx.fill();
          }
          ctx.restore();
        }
      };
      ring(false);
      ctx.lineWidth = 1;
      for (const m of motes) {
        const x = W / 2 + Math.cos(m.a + tt * m.s) * W * m.r, y = H / 2 + Math.sin(m.a + tt * m.s * 0.8) * H * m.r * 0.9;
        ctx.fillStyle = `rgba(46, 170, 196, ${0.35 + 0.3 * Math.sin(tt * 1.5 + m.a) ** 2})`;
        ctx.beginPath(); ctx.arc(x, y, 1.6, 0, 7); ctx.fill();
      }
      ctx.lineWidth = 1;
      // the brain's body: a filled, lit shape (deep blue at the back, violet at the front), then the neurons on it
      const body2 = (poly, a) => {
        if (!poly.length) return;
        const xs = poly.map((p) => p.sx), lo = Math.min(...xs), hi = Math.max(...xs);
        const fill = ctx.createLinearGradient(lo, 0, hi, 0);
        fill.addColorStop(0, `rgba(48, 70, 230, ${a})`); fill.addColorStop(0.55, `rgba(92, 70, 235, ${a})`); fill.addColorStop(1, `rgba(150, 80, 240, ${a})`);
        ctx.save(); ctx.shadowColor = "rgba(110, 100, 255, .65)"; ctx.shadowBlur = 38;
        ctx.fillStyle = fill; ctx.beginPath(); ctx.moveTo(poly[0].sx, poly[0].sy);
        for (const p of poly.slice(1)) ctx.lineTo(p.sx, p.sy);
        ctx.closePath(); ctx.fill(); ctx.restore();
      };
      body2(stem, 0.32);
      body2(cereb, 0.78);
      body2(sil, 0.82);
      const shine = ctx.createRadialGradient(cx0 + R * 0.1, cy0 - R * 0.2, 0, cx0 + R * 0.1, cy0 - R * 0.2, R * 0.75);
      shine.addColorStop(0, "rgba(190, 200, 255, .45)"); shine.addColorStop(1, "rgba(190, 200, 255, 0)");
      ctx.fillStyle = shine; ctx.beginPath(); ctx.moveTo(sil[0].sx, sil[0].sy); for (const p of sil.slice(1)) ctx.lineTo(p.sx, p.sy); ctx.closePath(); ctx.fill();
      const hue = (p) => { const t = Math.max(0, Math.min(1, (p.x + 1) / 2)); return [Math.round(150 + 70 * t), Math.round(190 - 40 * t), 255]; };
      for (const [i, j] of edges) {
        const p = pts[i], q = pts[j], d = (p.d + q.d) / 2, [r, gg, b] = hue(p);
        ctx.strokeStyle = `rgba(${r}, ${gg}, ${b}, ${0.1 + 0.3 * d})`;
        ctx.beginPath(); ctx.moveTo(p.sx, p.sy); ctx.lineTo(q.sx, q.sy); ctx.stroke();
      }
      ctx.save();
      for (const p of pts) {
        p.glow = Math.max(0, p.glow - dt * 1.4);
        const r = (W < 360 ? 0.6 : 0.9) + (W < 360 ? 1.1 : 1.7) * p.d + p.glow * (W < 360 ? 2.4 : 3.6);
        const [cr, cg, cb] = hue(p);
        ctx.fillStyle = p.glow > 0.03 ? `rgba(255, 170, 60, ${0.6 + 0.4 * p.glow})`
          : p.k === 3 ? `rgba(235, 240, 255, ${0.45 + 0.5 * p.d})`
          : `rgba(${cr}, ${cg}, ${cb}, ${0.18 + 0.5 * p.d})`;
        ctx.beginPath(); ctx.arc(p.sx, p.sy, r, 0, 7); ctx.fill();
        if (p.glow > 0.3) {
          const h = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, 10 * p.glow);
          h.addColorStop(0, `rgba(255, 190, 90, ${0.55 * p.glow})`); h.addColorStop(1, "rgba(255, 190, 90, 0)");
          ctx.fillStyle = h; ctx.beginPath(); ctx.arc(p.sx, p.sy, 10 * p.glow, 0, 7); ctx.fill();
        }
      }
      ctx.restore();
      ring(true);
      if (!still) {
        heat = Math.max(0, heat - dt * 0.5);
        const rate = 12 + heat * 40;
        for (let k = 0; k < 3; k++) if (Math.random() < dt * rate / 3) fire();
        const next = [];
        for (const pu of pulses) {
          pu.t += dt * pu.v * 3;
          const p = pts[pu.a], q = pts[pu.b];
          if (pu.t >= 1) {
            q.glow = 1;
            if (pu.hops > 0 && adj[pu.b].length) next.push({ ...pu, a: pu.b, b: adj[pu.b][Math.floor(Math.random() * adj[pu.b].length)], t: 0, hops: pu.hops - 1 });
            continue;
          }
          const back = Math.max(0, pu.t - 0.35);
          ctx.strokeStyle = `rgba(${pu.c}, .95)`; ctx.lineWidth = 1.8;
          ctx.beginPath(); ctx.moveTo(p.sx + (q.sx - p.sx) * back, p.sy + (q.sy - p.sy) * back);
          ctx.lineTo(p.sx + (q.sx - p.sx) * pu.t, p.sy + (q.sy - p.sy) * pu.t); ctx.stroke();
          ctx.lineWidth = 1;
          next.push(pu);
        }
        pulses = next.slice(-260);
      }
      raf = 0;
      if (!still) start();
    }
    function start() { if (!raf && !document.hidden && body.dataset.state === "idle" && cv.getClientRects().length) { raf = requestAnimationFrame(draw); } }
    build();
    last = performance.now();
    if (still) draw(last); else start();
    let rt;
    const rebuild = () => { clearTimeout(rt); rt = setTimeout(() => { build(); if (still) draw(performance.now()); else start(); }, 60); };
    window.addEventListener("resize", rebuild);
    if (window.ResizeObserver) new ResizeObserver(() => { if (cv.clientWidth !== W || cv.clientHeight !== H) rebuild(); }).observe(cv);
    document.addEventListener("visibilitychange", start);
    return { excite() { heat = Math.min(1, heat + 0.1); start(); } };
  })();
  let thinkTimer;
  input.addEventListener("input", () => {
    neural.excite();
    body.classList.add("thinking");                     // the labels light up in turn while the person types
    clearTimeout(thinkTimer);
    thinkTimer = setTimeout(() => body.classList.remove("thinking"), 2500);
  });

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
  let voiceLang = "en-IN";
  try { voiceLang = localStorage.getItem("ge_voice_lang") || "en-IN"; } catch (_) { /* storage blocked */ }
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
  // a form in the panel (signup, login, codes): only the X closes it, so a stray click outside never loses what was typed
  const drawerHasForm = () => !!drawerBody.querySelector("form");
  drawer.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]") || (e.target === drawer && !drawerHasForm())) drawer.hidden = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawerHasForm()) drawer.hidden = true; });

  // ---------------------------------------------------------------- free account
  let me = null;
  const accountBtn = $("#account");
  const accountDot = $("#account-dot");
  function showMe(user) {
    me = user;
    window.GE_me = user;
    accountDot.hidden = !user;
    accountDot.textContent = user ? "✓" : "";
    accountBtn.setAttribute("aria-label", user ? `Your account (${user.username})` : "Log in or create a free account");
    accountBtn.title = user ? user.username : "Account";
    document.dispatchEvent(new CustomEvent("ge:account", { detail: user }));
  }
  window.GE_meReady = api("/api/account/me").then((d) => { window.GE_dbSoon = d.db_connect === false; showMe(d.user); }).catch(() => {});
  document.addEventListener("ge:dbarea", (e) => {
    if (e.detail === "soon") { window.GE_dbSoon = true; return; }
    if (e.detail === "verify") { openAccount("verify"); return; }
    if (!drawer.hidden) return;
    openDrawer([el("h2", "acc-h", "Database area"), ...window.GE_dbUnlock(false, () => { drawer.hidden = true; refreshGoal(); })]);
  });

  function accForm(cls) { const f = el("form", "expert-form acc " + (cls || "")); f.noValidate = true; return f; }
  function accField(label, input, hint) {
    const l = el("label", "f"); l.append(el("span", null, label), input);
    if (hint) l.append(el("small", "hint", hint));
    return l;
  }
  function accInput(type, auto, name, max) {
    const i = el("input"); i.type = type; i.autocomplete = auto; i.name = name; i.maxLength = max || 120; return i;
  }
  // password with a Show button: a typo at signup is the usual reason a login "doesn't match" later
  function accPassField(label, input, hint) {
    const l = accField(label, input, hint);
    const wrap = el("div", "pass-wrap");
    const eye = el("button", "pass-eye", "Show"); eye.type = "button";
    eye.setAttribute("aria-label", "Show password");
    eye.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      eye.textContent = show ? "Hide" : "Show";
      eye.setAttribute("aria-label", show ? "Hide password" : "Show password");
      input.focus();
    });
    input.replaceWith(wrap);
    wrap.append(input, eye);
    return l;
  }
  async function accSubmit(form, go, err, fields, run) {
    go.disabled = true; err.hidden = true;
    for (const i of form.querySelectorAll("[aria-invalid]")) i.removeAttribute("aria-invalid");
    try { await run(); } catch (ex) {
      err.textContent = ex.message; err.hidden = false; go.disabled = false;
      const bad = ex.data && ex.data.field && fields[ex.data.field];
      if (bad) { bad.setAttribute("aria-invalid", "true"); bad.focus(); }
    }
  }
  function accTabs(active) {
    const row = el("div", "how acc-tabs");
    for (const [k, label] of [["signup", "Create free account"], ["login", "Log in"]]) {
      const b = el("button", "pill", label); b.type = "button"; b.setAttribute("aria-pressed", String(k === active));
      b.addEventListener("click", () => openAccount(k));
      row.append(b);
    }
    return row;
  }

  function signupView() {
    const form = accForm();
    const f = {
      full_name: accInput("text", "name", "full_name"), phone: accInput("tel", "tel", "phone", 20),
      email: accInput("email", "email", "email", 190), location: accInput("text", "address-level2", "location"),
      username: accInput("text", "username", "username", 30), password: accInput("password", "new-password", "password", 128),
      why: el("textarea"),
    };
    f.phone.placeholder = "+91 98xxx xxxxx"; f.why.maxLength = 2000; f.why.name = "why";
    f.why.placeholder = "e.g. I run a clothing shop and keep stock and sales in Excel. Our website uses MySQL.";
    const trap = el("input"); trap.name = "website"; trap.tabIndex = -1; trap.autocomplete = "off"; trap.className = "trap"; trap.setAttribute("aria-hidden", "true");
    const go = el("button", "btn go", "Create free account"); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true; err.setAttribute("role", "alert");
    form.append(
      accField("Full name", f.full_name), accField("Phone", f.phone), accField("Email", f.email, "We'll send a 6-digit code to confirm it."),
      accField("City or country", f.location), accField("Username", f.username),
      accPassField("Password", f.password, "At least 10 characters. Tap Show to check it."),
      accField("Why are you here?", f.why, "Your work or business, in a sentence or two. The agents use it to fit their answers to you."),
      trap, err, go, el("p", "fine", "Free. No card, no UPI."));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      accSubmit(form, go, err, f, async () => {
        const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.value]));
        body.website = trap.value;
        const d = await api("/api/account/signup", { method: "POST", body: JSON.stringify(body) });
        showMe(d.user);
        openAccount("verify", { sent: d.code_sent });
      });
    });
    return [el("h2", "acc-h", "Your free account"), el("p", "acc-sub", "Keep your chats on every device. Connect your database and get answers checked on it."),
            accTabs("signup"), form];
  }

  function loginView() {
    const form = accForm();
    const f = { login: accInput("text", "username", "login", 190), password: accInput("password", "current-password", "password", 128) };
    const go = el("button", "btn go", "Log in"); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true; err.setAttribute("role", "alert");
    const forgot = el("button", "linkish", "Forgot password?"); forgot.type = "button";
    forgot.addEventListener("click", () => openAccount("forgot"));
    form.append(accField("Email or username", f.login), accPassField("Password", f.password), err, go, forgot);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      accSubmit(form, go, err, f, async () => {
        const d = await api("/api/account/login", { method: "POST", body: JSON.stringify({ login: f.login.value, password: f.password.value }) });
        showMe(d.user);
        location.reload();          // this account's chats
      });
    });
    return [el("h2", "acc-h", "Welcome back"), accTabs("login"), form];
  }

  function codeForm(onCode, label) {
    const form = accForm();
    const code = accInput("text", "one-time-code", "code", 7); code.inputMode = "numeric"; code.pattern = "[0-9]*"; code.className = "code-in";
    const go = el("button", "btn go", label || "Confirm"); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true; err.setAttribute("role", "alert");
    form.append(accField("6-digit code", code), err, go);
    form.addEventListener("submit", (e) => { e.preventDefault(); accSubmit(form, go, err, { code }, () => onCode(code.value)); });
    setTimeout(() => code.focus(), 30);
    return form;
  }

  function verifyView(opts) {
    const note = el("p", "acc-sub", opts && opts.sent === false
      ? "Your account is ready, but the email didn't go out. Tap 'Send the code again'."
      : `We sent a 6-digit code to ${me ? me.email : "your email"}. It works for 10 minutes.`);
    const form = codeForm(async (code) => {
      const d = await api("/api/account/verify", { method: "POST", body: JSON.stringify({ code }) });
      showMe(d.user);
      openAccount("me", { welcome: true });
    }, "Confirm email");
    const again = el("button", "linkish", "Send the code again"); again.type = "button";
    again.addEventListener("click", async () => {
      again.disabled = true;
      try { await api("/api/account/resend", { method: "POST" }); note.textContent = "Sent. Check your inbox (and spam)."; }
      catch (ex) { note.textContent = ex.message; }
      setTimeout(() => { again.disabled = false; }, 30000);
    });
    return [el("h2", "acc-h", "Confirm your email"), note, form, again];
  }

  function forgotView() {
    const form = accForm();
    const email = accInput("email", "email", "email", 190);
    const go = el("button", "btn go", "Send me a code"); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true; err.setAttribute("role", "alert");
    form.append(accField("Your account's email", email), err, go);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      accSubmit(form, go, err, { email }, async () => {
        await api("/api/account/forgot", { method: "POST", body: JSON.stringify({ email: email.value }) });
        openAccount("reset", { email: email.value });
      });
    });
    return [el("h2", "acc-h", "Forgot password"), el("p", "acc-sub", "We'll email you a code to set a new password."), form];
  }

  function resetView(opts) {
    const form = accForm();
    const f = { code: accInput("text", "one-time-code", "code", 7), password: accInput("password", "new-password", "password", 128) };
    f.code.inputMode = "numeric"; f.code.className = "code-in";
    const go = el("button", "btn go", "Set new password"); go.type = "submit";
    const err = el("p", "note err"); err.hidden = true; err.setAttribute("role", "alert");
    form.append(accField("6-digit code", f.code), accPassField("New password", f.password, "At least 10 characters."), err, go);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      accSubmit(form, go, err, f, async () => {
        const d = await api("/api/account/reset", { method: "POST", body: JSON.stringify({ email: opts.email, code: f.code.value, password: f.password.value }) });
        showMe(d.user);
        location.reload();
      });
    });
    return [el("h2", "acc-h", "Set a new password"),
            el("p", "acc-sub", `If ${opts.email} has an account, a code is on its way. It works for 10 minutes.`), form];
  }

  function meView(opts) {
    const out = [el("h2", "acc-h", opts && opts.welcome ? "You're all set" : `Hi, ${(me.full_name || "").split(" ")[0]}`)];
    if (!me.email_verified) {
      const b = el("button", "btn solid", "Confirm your email"); b.type = "button";
      b.addEventListener("click", () => openAccount("verify"));
      out.push(el("p", "acc-sub", "Confirm your email to connect a database."), b);
    } else {
      out.push(el("p", "acc-sub", "Your chats are saved to your account and show on every device you log in on."));
    }
    const dl = el("dl", "acc-me");
    for (const [k, label] of [["username", "Username"], ["email", "Email"], ["phone", "Phone"], ["location", "City"]]) {
      dl.append(el("dt", null, label), el("dd", null, me[k] || ""));
    }
    const out2 = el("button", "btn", "Log out"); out2.type = "button";
    out2.addEventListener("click", async () => { await api("/api/account/logout", { method: "POST" }); location.href = "/"; });
    out.push(dl, out2);
    return out;
  }

  const ACC_VIEWS = { signup: signupView, login: loginView, verify: verifyView, forgot: forgotView, reset: resetView, me: meView };
  function openAccount(view, opts) {
    if (!view) view = me ? "me" : "signup";
    if (view === "me" && !me) view = "login";
    openDrawer(ACC_VIEWS[view](opts || {}));
    const first = drawerBody.querySelector("input:not(.trap)");
    if (first && view !== "verify") setTimeout(() => first.focus(), 30);
  }
  window.GE_openAccount = openAccount;
  accountBtn.addEventListener("click", () => openAccount());
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-account]");
    if (t) { e.preventDefault(); openAccount(t.dataset.account || undefined); }
  });

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
    try { localStorage.removeItem("ge_chat"); } catch (_) { /* fine */ }
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
      if (!state.messages.length) setIdle();          // remembered a chat that isn't there any more (new chat, logout, deleted)
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
