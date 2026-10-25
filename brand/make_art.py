"""Аватарка и баннер Кружка — из герба «Чисто».

Берём щит Чисто (brand/kit/crest-transparent.png из github.com/kozliq/chisto, рядом с этой папкой): рамка та же, поле перекрашиваем заново,
кобру убираем, а мангуст прыгает сквозь красное кольцо — кружок. Без надписей.

Нужны Pillow и numpy (в обычный Python бота не ставим):
  python3 -m venv /tmp/art && /tmp/art/bin/pip install pillow numpy && /tmp/art/bin/python make_art.py
"""
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
KIT = HERE.parent.parent / "chisto" / "brand" / "kit"
BG = (0x1c, 0x1a, 0x1b)
FIELD_L, FIELD_R = (96, 96, 96), (62, 62, 62)     # поле щита: левая половина светлее, как у Чисто
INK = (8, 8, 8)
RED, RED_DARK = (0x87, 0x37, 0x30), (0x67, 0x2a, 0x25)
INSET = 64          # от края щита до поля (рамка с двумя чёрными линиями), px исходника
PAD = 40            # поля вокруг исходника
SS = 4              # рисуем кольцо в 4 раза крупнее и уменьшаем — края гладкие


def erode(mask, px):
    """Сжать маску на px пикселей (чтобы получить поле внутри рамки)."""
    for _ in range(px // 2):
        mask = mask.filter(ImageFilter.MinFilter(5))
    return mask


def bfs(ok, seeds):
    """Все точки, куда можно дойти от seeds по ok (соседи по 4 сторонам)."""
    h, w = ok.shape
    seen = np.zeros_like(ok)
    q = deque(s for s in seeds if ok[s])
    for s in q:
        seen[s] = True
    while q:
        y, x = q.popleft()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and ok[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((ny, nx))
    return seen


def mongoose(src, field, split):
    """Вырезать мангуста: всё, что на поле не похоже на поле и связано с его телом, + дырки внутри."""
    rgb = src[..., :3].astype(int)
    want = np.where(np.arange(src.shape[1])[None, :, None] < split, FIELD_L, FIELD_R)
    off = np.abs(rgb - want).max(-1) > 14
    red = (rgb[..., 0] - rgb[..., 1] > 30)
    body = off & ~red & field
    # мангуст — над коброй; кобру отсекаем по красному и по месту (голова кобры справа ниже лап)
    h, w = body.shape
    yy, xx = np.mgrid[:h, :w]
    body &= ~((yy > 480 + PAD) & (xx > 430 + PAD))
    body &= yy < 860 + PAD
    blob = bfs(body, [(y + PAD, x + PAD) for y, x in ((300, 420), (350, 300), (420, 500))])
    outside = bfs(~blob, [(0, 0), (h - 1, w - 1), (0, w - 1), (h - 1, 0)])
    return blob | ~outside


def ring(size, box, width, part):
    """Кольцо-обруч (эллипс): part="back" — дальняя половина, "front" — ближняя. Красное с чёрным контуром."""
    big = Image.new("RGBA", (size[0] * SS, size[1] * SS))
    d = ImageDraw.Draw(big)
    x0, y0, x1, y1 = [v * SS for v in box]
    wd = width * SS
    for grow, color in ((wd // 2 + 3 * SS, INK), (wd // 2, RED)):
        d.ellipse((x0 - grow, y0 - grow, x1 + grow, y1 + grow), outline=color, width=grow * 2)
    # тонкая светлая гравировочная линия по кольцу — как блики на рамке щита
    inset = wd // 4
    d.ellipse((x0 - inset, y0 - inset, x1 + inset, y1 + inset), outline=RED_DARK, width=max(SS, wd // 8))
    half = Image.new("L", big.size, 0)
    cx = (x0 + x1) // 2
    ImageDraw.Draw(half).rectangle((0, 0, cx, big.size[1]) if part == "back" else (cx, 0, big.size[0], big.size[1]),
                                   fill=255)
    big.putalpha(ImageChops.multiply(big.getchannel("A"), half))
    return big.resize(size, Image.LANCZOS)


def crest():
    raw = Image.open(KIT / "crest-transparent.png").convert("RGBA")
    # щит касается краёв картинки — даём поля, иначе рамка по бокам не «сжимается» в поле
    src_img = Image.new("RGBA", (raw.width + 2 * PAD, raw.height + 2 * PAD))
    src_img.paste(raw, (PAD, PAD))
    src = np.array(src_img)
    w, h = src_img.size
    # внутри щита у исходника местами полупрозрачно — щит = всё, куда не дойти снаружи по прозрачному
    see = np.array(src_img.getchannel("A")) < 40
    outside = bfs(see, [(0, 0), (h - 1, w - 1), (0, w - 1), (h - 1, 0)])
    alpha = Image.fromarray(np.where(outside, 0, 255).astype(np.uint8))
    src[..., 3] = np.where(outside, src[..., 3], 255)
    field_img = erode(alpha, INSET)
    field = np.array(field_img) > 0
    top = np.array(alpha)
    split = int(top[np.argmax(top.any(1))].nonzero()[0].mean())     # остриё сверху = середина щита
    beast = mongoose(src, field, split)

    out = src.copy()
    # хвост кобры заходит на рамку справа внизу — там ставим зеркальный кусок левой рамки
    rgb = src[..., :3].astype(int)
    reddish = (rgb[..., 0] - rgb[..., 1] > 30) & ~field & (np.array(alpha) > 0)
    if reddish.any():
        ys, xs = np.nonzero(reddish)
        y0, y1, x0, x1 = ys.min() - 20, ys.max() + 20, xs.min() - 20, xs.max() + 20
        mirror = out[y0:y1, 2 * split - x1 + 1:2 * split - x0 + 1][:, ::-1].astype(float)
        right = out[y0:y1, x0:x1]
        band = ~field[y0:y1, x0:x1] & (np.array(alpha)[y0:y1, x0:x1] > 0)
        clean = band & ~reddish[y0:y1, x0:x1]
        k = right[clean, :3].mean() / max(1, mirror[clean, :3].mean())     # правая сторона рамки темнее
        right[band, :3] = np.clip(mirror[band, :3] * k, 0, 255).astype(np.uint8)
    xs = np.arange(w)[None, :]
    out[..., :3] = np.where(field[..., None], np.where(xs[..., None] < split, FIELD_L, FIELD_R), out[..., :3])
    line = field & ~(np.array(erode(field_img, 4)) > 0)       # тонкая чёрная линия по краю поля, как у Чисто
    out[line, :3] = INK
    base = Image.fromarray(out)

    # мангуст чуть уменьшен и поставлен в центр поля, кольцо — вокруг его поясницы
    ys, xs_ = np.nonzero(beast)
    bx0, by0, bx1, by1 = xs_.min(), ys.min(), xs_.max() + 1, ys.max() + 1
    m = src.copy()
    m[..., 3] = np.where(beast, 255, 0)
    m_img = Image.fromarray(m).crop((bx0, by0, bx1, by1))
    scale = 1.0
    m_img = m_img.resize((int(m_img.width * scale), int(m_img.height * scale)), Image.LANCZOS)
    px = (w - m_img.width) // 2 + 6
    py = int(h * 0.49 - m_img.height / 2)

    rw, rh = int(w * 0.24), int(h * 0.42)
    cx, cy = px + int(m_img.width * 0.46), py + int(m_img.height * 0.40)
    box = (cx - rw // 2, cy - rh // 2, cx + rw // 2, cy + rh // 2)
    layer = Image.new("RGBA", (w, h))
    layer.alpha_composite(ring((w, h), box, 30, "back"))
    layer.alpha_composite(m_img, (px, py))
    layer.alpha_composite(ring((w, h), box, 30, "front"))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), field_img))   # ничего не вылезает за рамку
    base.alpha_composite(layer)
    return base.crop(base.getbbox())


def on_bg(img, size, height_share):
    canvas = Image.new("RGBA", size, BG + (255,))
    k = size[1] * height_share / img.height
    s = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    canvas.alpha_composite(s, ((size[0] - s.width) // 2, (size[1] - s.height) // 2))
    return canvas.convert("RGB")


def main():
    c = crest()
    c.save(HERE / "crest-krug-transparent.png")
    on_bg(c, (640, 640), 0.80).save(HERE / "avatar-640.jpg", quality=92)
    on_bg(c, (640, 640), 0.80).save(HERE / "avatar-640.png")
    on_bg(c, (640, 360), 0.84).save(HERE / "tg-banner-640x360.png")
    on_bg(c, (1280, 720), 0.84).save(HERE / "tg-banner-1280x720.png")
    print("готово:", ", ".join(p.name for p in sorted(HERE.glob("*.png")) + sorted(HERE.glob("*.jpg"))))


if __name__ == "__main__":
    main()
