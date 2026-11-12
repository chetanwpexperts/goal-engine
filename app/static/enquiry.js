(() => {
  const form = document.getElementById("form");
  const err = document.getElementById("err");
  const send = document.getElementById("send");
  const opened = Date.now();
  const params = new URLSearchParams(location.search);

  // ?about=dbaba (or sheetu, website, workspace, automation) ticks that box for people coming from that part of the site
  const about = params.get("about");
  if (about) for (const box of form.querySelectorAll('input[name="interest"]')) if (box.value === about) box.checked = true;

  let capToken = "";
  async function newCaptcha() {
    const img = document.getElementById("capImg");
    try {
      const r = await fetch("/api/enquiry/captcha", { cache: "no-store" });
      const d = await r.json();
      capToken = d.token; img.src = d.image;
    } catch (_) { capToken = ""; }
    form.elements.captcha.value = "";
  }
  document.getElementById("capNew").addEventListener("click", () => { newCaptcha(); form.elements.captcha.focus(); });
  newCaptcha();

  const show = (msg, field) => {
    err.textContent = msg; err.hidden = !msg;
    for (const i of form.querySelectorAll("[aria-invalid]")) i.removeAttribute("aria-invalid");
    if (field) { const i = form.elements[field]; if (i) { i.setAttribute("aria-invalid", "true"); i.focus(); } }
  };

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = form.elements;
    const body = {
      name: f.name.value.trim(), email: f.email.value.trim(), phone: f.phone.value.trim(), company: f.company.value.trim(),
      role: f.role.value, team: f.team.value, message: f.message.value.trim(), consent: f.consent.checked,
      interest: [...form.querySelectorAll('input[name="interest"]:checked')].map((b) => b.value),
      captcha_token: capToken, captcha: f.captcha.value.trim(),
      website: f.website.value, elapsed_ms: Date.now() - opened, source: (params.get("from") || about || document.referrer.replace(location.origin, "") || "").slice(0, 60),
    };
    if (body.name.length < 2) return show("Please tell us your name.", "name");
    if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(body.email)) return show("Please give an email address we can reply to.", "email");
    if (!body.consent) return show("Please tick the box so we may contact you about your enquiry.", "consent");
    if (body.message.length < 5 && !body.interest.length) return show("Tell us a little about what you need, or pick what you're interested in.", "message");
    if (body.captcha.replace(/\s/g, "").length < 5) return show("Please type the 5 characters in the picture.", "captcha");
    show("");
    send.disabled = true; send.textContent = "Sending…";
    try {
      const r = await fetch("/api/enquiry", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        if (data.captcha) { await newCaptcha(); form.elements.captcha.focus(); }
        throw new Error(data.error || "Couldn't send it. Please try again.");
      }
      document.getElementById("doneEmail").textContent = body.email;
      form.hidden = true;
      const done = document.getElementById("done"); done.hidden = false; done.focus();
    } catch (x) {
      show(x.message || "Couldn't send it. Please try again.");
    } finally {
      send.disabled = false; send.textContent = "Send enquiry";
    }
  });
})();
