"""Draws the home page brain as a sharp, high-resolution image (no AI, no stock photo): folds grown with a
reaction-diffusion pattern inside a brain outline, then lit like a 3D surface. Output: app/static/brain-hd.png

    .venv/bin/python scripts/make_brain.py
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

GW, GH = 400, 277            # simulation grid (smaller = wider folds)
OUT_W, OUT_H = 2080, 1440    # final image (4x)
rng = np.random.default_rng(7)

OUT = [(-0.93, -0.02), (-0.96, 0.2), (-0.86, 0.43), (-0.66, 0.62), (-0.38, 0.74), (-0.05, 0.79), (0.28, 0.73), (0.55, 0.58),
       (0.78, 0.36), (0.92, 0.1), (0.95, -0.12), (0.85, -0.29), (0.66, -0.35), (0.5, -0.3), (0.42, -0.36), (0.32, -0.5),
       (0.12, -0.6), (-0.12, -0.6), (-0.32, -0.52), (-0.5, -0.42), (-0.7, -0.32), (-0.86, -0.18)]


def to_px(x, y, w, h, s=None):
    s = s or min(w / 2.3, h / 1.9)
    return w / 2 + x * s, h * 0.42 - y * s * 0.84


def smooth_poly(pts, n=24):
    out = []
    P = lambda i: pts[i % len(pts)]
    for i in range(len(pts)):
        p0, p1, p2, p3 = P(i - 1), P(i), P(i + 1), P(i + 2)
        for t in np.linspace(0, 1, n, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    return out


def mask(w, h, kind):
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    if kind == "cerebrum":
        d.polygon([to_px(x, y, w, h) for x, y in smooth_poly(OUT)], fill=255)
    elif kind == "cerebellum":
        cx, cy = to_px(-0.47, -0.47, w, h); s = min(w / 2.3, h / 1.9)
        d.ellipse([cx - 0.3 * s, cy - 0.15 * s, cx + 0.3 * s, cy + 0.15 * s], fill=255)
    else:
        d.polygon([to_px(x, y, w, h) for x, y in smooth_poly([(-0.04, -0.45), (0.02, -0.55), (-0.06, -0.8), (-0.1, -0.95),
                                                               (-0.17, -0.94), (-0.18, -0.7), (-0.2, -0.52)], 12)], fill=255)
    return np.asarray(im, dtype=np.float32) / 255


def gray_scott(w, h, steps=7000, F=0.038, k=0.061):
    U = np.ones((h, w), np.float32); V = np.zeros((h, w), np.float32)
    for _ in range(140):                             # seed spots
        x, y = rng.integers(0, w - 6), rng.integers(0, h - 6)
        U[y:y + 6, x:x + 6] = 0.5; V[y:y + 6, x:x + 6] = 0.25
    V += rng.random((h, w), dtype=np.float32) * 0.02
    lap = lambda Z: (np.roll(Z, 1, 0) + np.roll(Z, -1, 0) + np.roll(Z, 1, 1) + np.roll(Z, -1, 1) - 4 * Z)
    for _ in range(steps):
        uvv = U * V * V
        U += 0.16 * lap(U) - uvv + F * (1 - U)
        V += 0.08 * lap(V) + uvv - (F + k) * V
    return V


def main():
    m_big = mask(GW, GH, "cerebrum")
    V = gray_scott(GW, GH)
    V = (V - V.min()) / (V.max() - V.min() + 1e-6)
    # up to full size, smooth: the folds become round ridges
    Hf = ndimage.zoom(V, (OUT_H / GH, OUT_W / GW), order=3)
    Hf = ndimage.gaussian_filter(Hf, 3.2)
    Hf = (Hf - Hf.min()) / (Hf.max() - Hf.min())
    ridge = np.clip((Hf - 0.18) / 0.5, 0, 1) ** 0.7

    mC = ndimage.gaussian_filter(mask(OUT_W, OUT_H, "cerebrum"), 2)
    mB = ndimage.gaussian_filter(mask(OUT_W, OUT_H, "cerebellum"), 2)
    mS = ndimage.gaussian_filter(mask(OUT_W, OUT_H, "stem"), 3)
    # the whole brain bulges out: a dome under the folds
    dist = ndimage.distance_transform_edt(mC > 0.5)
    dome = np.clip(dist / (OUT_W * 0.2), 0, 1) ** 0.5
    yy, xx = np.mgrid[0:OUT_H, 0:OUT_W].astype(np.float32)
    s = min(OUT_W / 2.3, OUT_H / 1.9); nx = (xx - OUT_W / 2) / s; ny = (OUT_H * 0.42 - yy) / (s * 0.84)

    # cerebellum: fine curved stripes
    rr = np.hypot((nx + 0.47) / 1.6, ny + 0.66)
    stripes = 0.5 + 0.5 * np.sin(rr * 260 + np.sin(nx * 11) * 0.8)
    stripes = ndimage.gaussian_filter(stripes, 1.5)
    distB = ndimage.distance_transform_edt(mB > 0.5); domeB = np.clip(distB / (OUT_W * 0.05), 0, 1) ** 0.5

    height = np.where(mC > 0.5, dome * 0.75 + ridge * 0.38, 0) + np.where((mB > 0.5) & (mC <= 0.5), domeB * 0.6 + stripes * 0.25, 0)
    height += mS * 0.35 * (mC <= 0.5) * (mB <= 0.5)
    gy, gx = np.gradient(ndimage.gaussian_filter(height, 1.2))
    k = 38.0
    nxv, nyv, nzv = -gx * k, -gy * k, np.ones_like(gx)
    nl = np.sqrt(nxv ** 2 + nyv ** 2 + nzv ** 2); nxv /= nl; nyv /= nl; nzv /= nl
    L = np.array([-0.45, -0.55, 0.7]); L /= np.linalg.norm(L)
    diff = np.clip(nxv * L[0] + nyv * L[1] + nzv * L[2], 0, 1)
    Hv = L + np.array([0, 0, 1.0]); Hv /= np.linalg.norm(Hv)
    spec = np.clip(nxv * Hv[0] + nyv * Hv[1] + nzv * Hv[2], 0, 1) ** 40

    # colour: deep navy in the grooves, electric blue on the ridges, violet at the front, amber glow at the back
    t = np.clip((nx + 1) / 2, 0, 1)
    deep = np.stack([8 + 30 * t, 10 + 4 * t, 70 + 50 * t], -1)
    blue = np.stack([40 + 120 * t, 95 - 20 * t, 255 - 5 * t], -1)
    hl = ridge if True else 0
    base = deep * (1 - hl[..., None]) + blue * hl[..., None]
    shade = (0.3 + 1.15 * diff)[..., None]
    col = base * shade + spec[..., None] * np.array([210, 220, 255]) * 0.9
    warm = np.clip(1 - np.hypot(nx + 0.8, (ny - 0.05) * 1.2) / 0.9, 0, 1) ** 1.3
    col = col * (1 - 0.65 * warm[..., None]) + np.array([255, 150, 70]) * (0.65 * warm[..., None]) * (0.35 + 1.0 * ridge[..., None]) * (0.5 + 0.8 * diff[..., None])
    front = np.clip(1 - np.hypot(nx - 0.55, ny - 0.15) / 0.8, 0, 1)
    col += np.array([60, 20, 90]) * front[..., None] * 0.6
    # rim light where the surface turns away
    rim = np.clip(1 - dist / (OUT_W * 0.02), 0, 1) * (mC > 0.5)
    col += np.array([120, 130, 255]) * rim[..., None] * 0.55
    # cerebellum + stem tints
    bmask = (mB * (1 - mC))[..., None]
    col = col * (1 - bmask * 0.25) + np.array([70, 80, 255]) * bmask * 0.25
    smask = (mS * (1 - mC) * (1 - mB))[..., None]
    sx = np.clip(1 - np.abs(nx + 0.08) / 0.1, 0, 1)[..., None]
    col = col * (1 - smask) + (np.array([40, 60, 200]) + np.array([90, 110, 55]) * sx) * smask
    # a bumpy edge: near the outline only the ridges stay, so the folds shape the silhouette
    edge = np.clip(dist / (OUT_W * 0.018), 0, 1)
    lobed = mC * np.clip(edge + np.clip((ridge - 0.25) / 0.3, 0, 1), 0, 1)
    alpha = np.clip(np.maximum(np.maximum(lobed, mB), mS * np.clip((ny + 1.0) / 0.3, 0, 1)), 0, 1)

    rgb = np.clip(col, 0, 255).astype(np.uint8)
    a = (alpha * 255).astype(np.uint8)
    img = Image.fromarray(np.dstack([rgb, a]), "RGBA")
    # tiny specks of light on the surface
    d = ImageDraw.Draw(img)
    for _ in range(420):
        x, y = rng.uniform(0, OUT_W), rng.uniform(0, OUT_H)
        if mC[int(y), int(x)] > 0.9 and ridge[int(y), int(x)] > 0.55:
            r = rng.uniform(1.5, 3.5)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(235, 240, 255, int(rng.uniform(120, 230))))
    # bloom: the light spills a little past the surface
    glow = img.filter(ImageFilter.GaussianBlur(28))
    ga = np.asarray(glow).astype(np.float32); ga[..., 3] *= 0.55
    base = Image.new("RGBA", img.size, (0, 0, 0, 0)); base.alpha_composite(Image.fromarray(ga.astype(np.uint8), "RGBA")); base.alpha_composite(img)
    img = base
    img = img.crop(img.getbbox())
    img.save("app/static/brain-hd.webp", quality=90, method=6)
    print(img.size)


if __name__ == "__main__":
    main()
