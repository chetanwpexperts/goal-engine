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
    if (!res.ok) throw new Error(data.error || data.detail || "Something went wrong. Try again.");
    return data;
  }

  // ---------------------------------------------------------------- composer
  const EXAMPLES = [
    "Jinka payment baaki hai unko har Monday reminder chala jaye",
    "Meri website slow hai, check karo",
    "Patients ko appointment se ek din pehle yaad dilao",
    "Roz subah 9 baje pending payments ki report chahiye",
    "Database ki query slow hai",
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

  function renderAgents(agents) {
    const box = $("#agents");
    box.replaceChildren();
    for (const a of agents || []) box.append(a.automation ? renderAutomation(a) : renderCheckAgent(a));
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
