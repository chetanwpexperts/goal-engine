// The home page brain, drawn in code so it stays sharp on every screen: a glowing side-view brain with folds,
// signals running along them, two lit orbits, and more activity while the person types.
(() => {
  const cv = document.getElementById("brainhd");
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext("2d");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const body = document.body;
  let W = 0, H = 0, S = 0, CX = 0, CY = 0, dpr = 1, art = null, folds = [], sparks = [], heat = 0, raf = 0, last = 0;
  const t0 = performance.now();

  // seeded random, so the brain looks the same on every visit
  let seed = 7;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };

  // side view, front to the right; y up
  const OUT = [[-0.93, -0.02], [-0.96, 0.2], [-0.86, 0.43], [-0.66, 0.62], [-0.38, 0.74], [-0.05, 0.79], [0.28, 0.73], [0.55, 0.58],
    [0.78, 0.36], [0.92, 0.1], [0.95, -0.12], [0.85, -0.29], [0.66, -0.35], [0.5, -0.3], [0.42, -0.36], [0.32, -0.5], [0.12, -0.6],
    [-0.12, -0.6], [-0.32, -0.52], [-0.5, -0.42], [-0.7, -0.32], [-0.86, -0.18]];
  const CER = { x: -0.5, y: -0.5, rx: 0.3, ry: 0.17 };
  const inside = (x, y) => {
    let ok = false;
    for (let i = 0, j = OUT.length - 1; i < OUT.length; j = i++) {
      const [xi, yi] = OUT[i], [xj, yj] = OUT[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) ok = !ok;
    }
    return ok;
  };
  const px = (x) => CX + x * S, py = (y) => CY - y * S;

  function smoothPath(c, pts, close) {
    c.beginPath();
    const n = pts.length;
    const P = (i) => pts[close ? (i + n) % n : Math.max(0, Math.min(n - 1, i))];
    c.moveTo(px(P(0)[0]), py(P(0)[1]));
    for (let i = 0; i < (close ? n : n - 1); i++) {
      const p0 = P(i - 1), p1 = P(i), p2 = P(i + 1), p3 = P(i + 2);
      c.bezierCurveTo(px(p1[0] + (p2[0] - p0[0]) / 6), py(p1[1] + (p2[1] - p0[1]) / 6),
                      px(p2[0] - (p3[0] - p1[0]) / 6), py(p2[1] - (p3[1] - p1[1]) / 6), px(p2[0]), py(p2[1]));
    }
    if (close) c.closePath();
  }

  // folds: smooth random walks that stay inside the outline
  function makeFolds() {
    seed = 7;
    const out = [];
    for (let k = 0; k < 150; k++) {
      let x = -0.9 + rnd() * 1.8, y = -0.55 + rnd() * 1.3;
      if (!inside(x, y)) { k--; continue; }
      let a = rnd() * Math.PI * 2, turn = (rnd() - 0.5) * 0.5;
      const pts = [[x, y]];
      const len = 8 + Math.floor(rnd() * 16);
      for (let s = 0; s < len; s++) {
        turn += (rnd() - 0.5) * 0.35; turn = Math.max(-0.45, Math.min(0.45, turn)); a += turn;
        const nx = x + Math.cos(a) * 0.035, ny = y + Math.sin(a) * 0.035;
        if (!inside(nx, ny)) { a += Math.PI * 0.6; continue; }
        x = nx; y = ny; pts.push([x, y]);
      }
      if (pts.length > 4) out.push(pts);
    }
    return out;
  }

  // colour across the brain: warm amber at the back, deep blue in the middle, violet at the front
  function tone(x, y, light) {
    const t = Math.max(0, Math.min(1, (x + 1) / 2));
    const warm = Math.max(0, 0.45 - t) / 0.45 * (y < 0.35 ? 1 : 0.6);
    const r = 40 + 90 * t + 190 * warm, g = 55 + 20 * t + 70 * warm, b = 225 + 25 * t - 120 * warm;
    const k = light;
    return `rgb(${Math.min(255, r + (255 - r) * k) | 0}, ${Math.min(255, g + (255 - g) * k) | 0}, ${Math.min(255, b + (255 - b) * k) | 0})`;
  }

  // the brain itself: a high-resolution image made by scripts/make_brain.py; light and motion are drawn live on top
  const img = new Image();
  img.decoding = "async";
  img.src = "/static/brain-hd.webp";
  let spots = [];
  function paintArt() {
    art = document.createElement("canvas");
    art.width = W * dpr; art.height = H * dpr;
    const c = art.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const halo = c.createRadialGradient(CX, CY, S * 0.2, CX, CY, S * 1.5);
    halo.addColorStop(0, "rgba(255, 255, 255, .9)"); halo.addColorStop(0.3, "rgba(170, 180, 255, .45)"); halo.addColorStop(1, "rgba(170, 150, 255, 0)");
    c.fillStyle = halo; c.fillRect(0, 0, W, H);
    if (!img.complete || !img.naturalWidth) return;
    const bw = S * 2.05, bh = bw * img.naturalHeight / img.naturalWidth;
    BX = CX - bw / 2; BY = CY - bh * 0.47; BW = bw; BH = bh;
    c.imageSmoothingQuality = "high";
    c.drawImage(img, BX, BY, bw, bh);
    // where signals may light up: points on the brain's surface
    const probe = document.createElement("canvas"); probe.width = 120; probe.height = Math.round(120 * bh / bw);
    const pc = probe.getContext("2d"); pc.drawImage(img, 0, 0, probe.width, probe.height);
    const d = pc.getImageData(0, 0, probe.width, probe.height).data;
    spots = [];
    for (let y = 0; y < probe.height; y += 2) for (let x = 0; x < probe.width; x += 2) {
      const i = (y * probe.width + x) * 4;
      if (d[i + 3] > 230 && d[i + 2] > 150 && y < probe.height * 0.78) spots.push([BX + (x / probe.width) * bw, BY + (y / probe.height) * bh]);
    }
  }
  let BX = 0, BY = 0, BW = 0, BH = 0;
  img.addEventListener("load", () => { if (W) { paintArt(); if (still) draw(performance.now()); else start(); } cv.classList.add("ready"); });

  function build() {
    dpr = Math.min(window.devicePixelRatio || 1, 3);
    W = cv.clientWidth; H = cv.clientHeight;
    if (!W || !H) return false;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    S = Math.min(W / 2.5, H / 2.15); CX = W / 2; CY = H * 0.44;
    paintArt();
    return true;
  }

  const ORBITS = [{ rx: 1.32, ry: 0.3, rot: -0.2, col: "255, 170, 80", sp: 0.45 }, { rx: 1.2, ry: 0.36, rot: 0.24, col: "90, 170, 255", sp: -0.35 }];
  function orbit(front, tt) {
    for (const o of ORBITS) {
      ctx.save(); ctx.translate(CX, CY + S * 0.05); ctx.rotate(o.rot);
      const g = ctx.createLinearGradient(-S * o.rx, 0, S * o.rx, 0);
      g.addColorStop(0, `rgba(${o.col}, 0)`); g.addColorStop(0.5, `rgba(${o.col}, ${front ? 0.9 : 0.35})`); g.addColorStop(1, `rgba(${o.col}, 0)`);
      ctx.strokeStyle = g; ctx.lineWidth = front ? Math.max(1.5, S * 0.012) : Math.max(1, S * 0.006);
      ctx.shadowColor = `rgba(${o.col}, .9)`; ctx.shadowBlur = front ? S * 0.04 : 0;
      ctx.beginPath(); ctx.ellipse(0, 0, S * o.rx, S * o.ry, 0, front ? 0 : Math.PI, front ? Math.PI : Math.PI * 2); ctx.stroke();
      const a = ((tt * o.sp) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
      if ((Math.sin(a) > 0) === front) {
        const x = Math.cos(a) * S * o.rx, y = Math.sin(a) * S * o.ry, r = S * 0.05;
        const d = ctx.createRadialGradient(x, y, 0, x, y, r);
        d.addColorStop(0, "rgba(255, 255, 255, 1)"); d.addColorStop(0.3, `rgba(${o.col}, .9)`); d.addColorStop(1, `rgba(${o.col}, 0)`);
        ctx.shadowBlur = 0; ctx.fillStyle = d; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
      }
      ctx.restore();
    }
  }

  function spawn() {
    if (!spots.length) return;
    const a = spots[Math.floor(Math.random() * spots.length)];
    let b = a;
    for (let k = 0; k < 12; k++) { const c = spots[Math.floor(Math.random() * spots.length)]; if (Math.hypot(c[0] - a[0], c[1] - a[1]) < S * 0.35) { b = c; break; } }
    sparks.push({ a, b, t: 0, v: 0.5 + Math.random() * 0.7, warm: Math.random() < 0.5 });
  }

  function draw(now) {
    const dt = Math.min(0.05, (now - last) / 1000 || 0.016); last = now;
    const tt = still ? 1.2 : (now - t0) / 1000;
    ctx.clearRect(0, 0, W, H);
    const breathe = still ? 1 : 1 + Math.sin(tt * 1.1) * 0.012;
    orbit(false, tt);
    ctx.save();
    ctx.translate(CX, CY); ctx.scale(breathe, breathe); ctx.translate(-CX, -CY + (still ? 0 : Math.sin(tt * 0.9) * S * 0.015));
    ctx.drawImage(art, 0, 0, W, H);
    // signals running along the folds
    ctx.globalCompositeOperation = "lighter";
    for (const s of sparks) {
      s.t += dt * s.v;
      const x = s.a[0] + (s.b[0] - s.a[0]) * s.t, y = s.a[1] + (s.b[1] - s.a[1]) * s.t;
      const fade = Math.sin(Math.min(1, s.t) * Math.PI), r = S * 0.05 * (0.6 + fade);
      if (s.b !== s.a) {                                 // the trail it leaves along the way
        ctx.strokeStyle = `rgba(${s.warm ? "255, 190, 110" : "170, 210, 255"}, ${0.55 * fade})`; ctx.lineWidth = Math.max(1, S * 0.006);
        ctx.beginPath(); ctx.moveTo(s.a[0] + (s.b[0] - s.a[0]) * Math.max(0, s.t - 0.35), s.a[1] + (s.b[1] - s.a[1]) * Math.max(0, s.t - 0.35));
        ctx.lineTo(x, y); ctx.stroke();
      }
      const col = s.warm ? "255, 180, 90" : "150, 200, 255";
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(255, 255, 255, ${0.95 * fade})`); g.addColorStop(0.25, `rgba(${col}, ${0.8 * fade})`); g.addColorStop(1, `rgba(${col}, 0)`);
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.restore();
    sparks = sparks.filter((s) => s.t < 1);
    orbit(true, tt);
    if (!still) {
      heat = Math.max(0, heat - dt * 0.6);
      const rate = 7 + heat * 35;
      if (Math.random() < dt * rate) spawn();
      raf = 0; start();
    }
  }
  function start() { if (!raf && !document.hidden && body.dataset.state === "idle" && cv.getClientRects().length) raf = requestAnimationFrame(draw); }
  const go = () => { if (build()) { last = performance.now(); if (still) { for (let i = 0; i < 6; i++) spawn(); draw(last); } else start(); } };
  go();
  let rt;
  const again = () => { clearTimeout(rt); rt = setTimeout(go, 80); };
  window.addEventListener("resize", again);
  if (window.ResizeObserver) new ResizeObserver(() => { if (cv.clientWidth !== W || cv.clientHeight !== H) again(); }).observe(cv);
  document.addEventListener("visibilitychange", start);
  new MutationObserver(start).observe(body, { attributes: true, attributeFilter: ["data-state"] });
  document.addEventListener("input", () => { heat = Math.min(1, heat + 0.15); start(); });
})();
