// Goal Engine UI. All user and AI text is inserted with textContent (never innerHTML).
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  const messagesEl = $("#messages");
  const intro = $("#intro");
  const input = $("#input");
  const send = $("#send");
  const card = $("#card");
  const cardEmpty = $("#card-empty");
  let currentGoalId = null;
  let wordTarget = 0;
  const optionsEl = $("#options");
  const wordcount = $("#wordcount");

  function countWords(text) {
    return (text.trim().match(/\S+/g) || []).length;
  }

  function updateWordcount() {
    if (!wordTarget) { wordcount.hidden = true; return; }
    const n = countWords(input.value);
    wordcount.hidden = false;
    wordcount.textContent = n >= wordTarget ? `${n} words ✓` : `${n} / ${wordTarget} words`;
    wordcount.classList.toggle("ok", n >= wordTarget);
  }

  function setAskWords(n) {
    wordTarget = n || 0;
    input.rows = wordTarget ? 5 : 2;
    input.placeholder = wordTarget
      ? "Kya kaam hai, kahan hai, kya problem hai, kab se… (likho ya 🎤 bolo)"
      : "What's the problem? Type it or tap 🎤";
    updateWordcount();
  }

  function showOptions(options) {
    optionsEl.replaceChildren();
    optionsEl.hidden = !(options && options.length);
    for (const label of options || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.addEventListener("click", () => sendMessage(label));
      optionsEl.appendChild(b);
    }
  }

  const RELATION_LABEL = {
    new_goal: "New goal",
    update_goal: "Goal updated",
    supporting_task: "Supporting task added",
    chit_chat: "",
    unknown: "",
    howto: "How to",
    corrected: "Corrected",
    safety: "",
    list: "",
  };
  const ACTION_LABEL = {
    ask: "Next: one question",
    investigate: "Next: check something",
    retrieve: "Next: get your data",
    execute: "Next: do the work",
    wait: "Next: wait",
  };

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...options,
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error(data.error || data.detail || "Something went wrong. Please try again.");
    return data;
  }

  function addMessage(role, text, tag) {
    intro.hidden = true;
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    if (tag) {
      const t = document.createElement("span");
      t.className = "tag";
      t.textContent = tag;
      el.appendChild(t);
    }
    el.appendChild(document.createTextNode(text));
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return el;
  }

  function renderGoals(goals) {
    const list = $("#goal-list");
    list.replaceChildren();
    $("#no-goals").hidden = goals.length > 0;
    for (const g of goals) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = g.status;
      b.setAttribute("aria-current", String(g.id === currentGoalId));
      const title = document.createElement("span");
      title.className = "g-title";
      title.textContent = g.title;
      const status = document.createElement("span");
      status.className = "g-status";
      status.textContent = g.status === "active" ? `#${g.id} · active` : `#${g.id} · ${g.status === "done" ? "solved" : "dropped"}`;
      b.append(title, status);
      b.addEventListener("click", () => openGoal(g.id));
      li.appendChild(b);
      list.appendChild(li);
    }
  }

  function fillList(ul, items, emptyText, render) {
    ul.replaceChildren();
    if (!items || !items.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = emptyText;
      ul.appendChild(li);
      return;
    }
    for (const item of items) ul.appendChild(render(item));
  }

  function textItem(text) {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  }

  function renderCard(goal, relation) {
    if (!goal) return;
    currentGoalId = goal.id;
    cardEmpty.hidden = true;
    card.hidden = false;

    const values = {
      id: goal.id,
      detected_language: goal.detected_language || "language unknown",
      title: goal.title,
      original_request: goal.original_request,
      interpreted_problem: goal.interpreted_problem || "Not clear yet.",
      desired_goal: goal.desired_goal || "Not clear yet.",
      next_type: ACTION_LABEL[goal.next_action.type] || "Next",
      next_description: goal.next_action.description || "",
      confidence_pct: `${Math.round(goal.confidence * 100)}%`,
    };
    for (const [key, value] of Object.entries(values)) {
      $$(`[data-f="${key}"]`, card).forEach((el) => { el.textContent = value; });
    }
    $("#conf-bar").style.width = `${Math.round(goal.confidence * 100)}%`;

    fillList($('[data-list="known_facts"]', card), goal.known_facts, "Nothing confirmed yet.", textItem);
    fillList($('[data-list="missing_info"]', card), goal.missing_info, "Nothing blocking. Ready for the next step.", (m) => {
      const li = document.createElement("li");
      li.textContent = m.question;
      if (m.why) {
        const why = document.createElement("small");
        why.textContent = m.why;
        li.appendChild(why);
      }
      return li;
    });
    fillList($('[data-list="constraints"]', card), goal.constraints, "", textItem);
    fillList($('[data-list="sub_goals"]', card), goal.sub_goals, "", textItem);
    for (const key of ["constraints", "sub_goals"]) {
      $(`[data-hide-empty="${key}"]`, card).hidden = !(goal[key] && goal[key].length);
    }

    const badge = $("#relation");
    const label = relation ? RELATION_LABEL[relation] : "";
    badge.textContent = label || (goal.status === "active" ? "Active" : goal.status === "done" ? "Solved" : "Dropped");
    badge.classList.remove("flash");
    if (label) { void badge.offsetWidth; badge.classList.add("flash"); }

    $$("[data-status]", card).forEach((b) => {
      const s = b.dataset.status;
      b.hidden = goal.status === "active" ? s === "active" : s !== "active";
    });
  }

  function setRemaining(n) {
    if (typeof n === "number") $("#remaining").textContent = `${n} messages left today.`;
  }

  async function openGoal(id) {
    try {
      const data = await api(`/api/goals/${id}`);
      renderCard(data.goal);
      const state = await api("/api/state");
      renderGoals(state.goals);
    } catch (err) {
      addMessage("error", err.message);
    }
  }

  async function sendMessage(text) {
    text = text.trim();
    if (!text) return;
    addMessage("user", text);
    input.value = "";
    showOptions([]);
    setAskWords(0);
    stopListening();
    send.disabled = true;
    const thinking = addMessage("assistant thinking", "Understanding…");
    try {
      const data = await api("/api/message", {
        method: "POST", body: JSON.stringify({ text, goal_id: currentGoalId }),
      });
      thinking.remove();
      addMessage("assistant", data.reply, RELATION_LABEL[data.relation] || "");
      if (data.goal) renderCard(data.goal, data.relation);
      showOptions(data.options || []);
      setAskWords(data.ask_words || 0);
      renderGoals(data.goals || []);
      setRemaining(data.remaining_today);
    } catch (err) {
      thinking.remove();
      addMessage("error", err.message);
    } finally {
      send.disabled = false;
      input.focus();
    }
  }

  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(input.value);
  });
  input.addEventListener("input", updateWordcount);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input.value);
    }
  });
  // Voice input: the browser's own speech recognition (free, no server cost).
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = $("#mic");
  const micLang = $("#mic-lang");
  let recognition = null;
  let listening = false;
  let voiceLang = "hi-IN";
  try { voiceLang = localStorage.getItem("ge_voice_lang") || "hi-IN"; } catch (_) { /* storage blocked */ }

  function stopListening() {
    if (recognition && listening) recognition.stop();
  }

  function paintMicLang() {
    micLang.textContent = voiceLang === "hi-IN" ? "हिं" : "EN";
    micLang.title = voiceLang === "hi-IN" ? "Voice: Hindi (tap for English)" : "Voice: English (tap for Hindi)";
  }

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
      const base = input.value ? input.value.trim() + " " : "";
      recognition.onresult = (event) => {
        let spoken = "";
        for (const result of event.results) spoken += result[0].transcript;
        input.value = base + spoken;
        updateWordcount();
      };
      recognition.onstart = () => { listening = true; mic.classList.add("listening"); };
      recognition.onend = () => { listening = false; mic.classList.remove("listening"); input.focus(); };
      recognition.onerror = (e) => {
        if (e.error === "not-allowed") addMessage("error", "Microphone permission is blocked. Allow it in the browser to speak.");
      };
      recognition.start();
    });
  }

  $("#new-chat").addEventListener("click", async () => {
    try { await api("/api/new-chat", { method: "POST" }); } catch (_) { /* reload anyway */ }
    window.location.reload();
  });

  $$(".example").forEach((b) => b.addEventListener("click", () => sendMessage(b.textContent)));

  $$("[data-status]").forEach((b) => b.addEventListener("click", async () => {
    if (!currentGoalId) return;
    try {
      const data = await api(`/api/goals/${currentGoalId}/status`, {
        method: "POST", body: JSON.stringify({ status: b.dataset.status }),
      });
      renderCard(data.goal);
      renderGoals(data.goals);
    } catch (err) {
      addMessage("error", err.message);
    }
  }));

  (async () => {
    try {
      const state = await api("/api/state");
      for (const m of state.messages) {
        if (m.role === "user") addMessage("user", m.text);
        else addMessage("assistant", m.text, RELATION_LABEL[m.relation] || "");
      }
      if (state.current) renderCard(state.current);
      renderGoals(state.goals);
      setRemaining(state.remaining_today);
    } catch (_) { /* first load offline: page still usable */ }
  })();
})();
