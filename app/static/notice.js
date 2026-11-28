// The beta notice: shown once per browser session, on whichever page a visitor opens first.
// sessionStorage keeps it to one showing until the tab or browser is closed; if storage is blocked it simply shows once
// per page load and can be closed like any other dialog.
(function () {
  "use strict";
  var KEY = "ge_beta_notice_seen";
  try { if (sessionStorage.getItem(KEY) === "1") return; } catch (e) { /* storage blocked: show it, it's harmless */ }

  function seen() { try { sessionStorage.setItem(KEY, "1"); } catch (e) { /* fine */ } }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  function show() {
    var back = el("div", "bn-back");
    var box = el("div", "bn");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-labelledby", "bn-title");

    var badge = el("p", "bn-badge", "Open beta");
    var title = el("h2", "bn-title", "You're here early. Welcome!");
    title.id = "bn-title";
    var p1 = el("p", "bn-text", "Goal Engine is in open beta. DBaba and Sheetu already find the real cause of database and spreadsheet problems, and every week they learn something new.");
    var p2 = el("p", "bn-text", "In the workshop right now:");
    var list = el("ul", "bn-list");
    ["One-click fixes you can always undo", "Nightly backups that are actually test-restored", "Alerts before a small problem becomes a 3 a.m. outage"].forEach(function (t) {
      list.appendChild(el("li", null, t));
    });
    var p3 = el("p", "bn-text", "No ChatGPT or outside AI is used: Goal Engine runs on its own models, so your data stays here. If something looks wrong, tell us; it gets fixed fast.");

    var row = el("div", "bn-row");
    var go = el("button", "bn-go", "Start exploring");
    go.type = "button";
    var talk = el("a", "bn-link", "Tell us what you need");
    talk.href = "/enquiry?from=beta-notice";
    row.appendChild(go);
    row.appendChild(talk);

    var x = el("button", "bn-x");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.textContent = "×";

    box.append(x, badge, title, p1, p2, list, p3, row);
    back.appendChild(box);
    document.body.appendChild(back);

    var before = document.activeElement;
    function close() {
      seen();
      back.remove();
      document.removeEventListener("keydown", onKey, true);
      if (before && before.focus) { try { before.focus({ preventScroll: true }); } catch (e) { /* fine */ } }
    }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key === "Tab") {                        // keep the keyboard inside the dialog while it is open
        var f = [x, go, talk], i = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
      }
    }
    go.addEventListener("click", close);
    x.addEventListener("click", close);
    talk.addEventListener("click", seen);
    back.addEventListener("click", function (e) { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey, true);
    go.focus({ preventScroll: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { setTimeout(show, 600); });
  else setTimeout(show, 600);
})();
