#!/usr/bin/env python3
"""
THE WOOLLY AFFAIRS -- 30s Instagram Reel renderer
=================================================

Turns the production package (`production_package.md`) + the caption file
(`assets/captions.srt`) into a finished vertical MP4.

What it implements, straight from the package:
  * The §2 shot-by-shot timeline (hook + 8 scenes + end card), exact starts.
  * Ken Burns motion (eased slow push / pull / pan) on every still  -- §8.
  * Animated scene-title overlays in Playfair/Poppins/script substitutes -- §12.
  * Burned-in VO captions parsed from the .srt                        -- §4.
  * Cross-dissolve transitions between scenes                         -- §2.
  * The "cozy-premium" warm grade: matte lift, warm balance, tamed
    reds, boosted golds, highlight bloom, vignette, fine grain        -- §9.
  * Animated end card: serif logo reveal + staggered CTA lines        -- §2/§12.
  * Optional VO / music / SFX muxing with music ducked under VO       -- §10/§11.

Because the source *photos* are not shipped with the package, any scene
whose image is missing renders an on-brand placeholder card so the whole
30s reel plays end-to-end today. Drop your real photos into `assets/`
using the filenames printed by `--list-assets` and re-run for the final.

Usage
-----
    python make_reel.py                      # render 1080x1920 @30fps -> out/woolly_reel.mp4
    python make_reel.py --fps 60             # buttery 60fps (package default)
    python make_reel.py --uhd                # 2160x3840 master (survives IG recompression)
    python make_reel.py --list-assets        # show the filename each scene looks for
    python make_reel.py --vo vo.wav --music music.mp3   # mux audio, music ducked

Requires: moviepy>=2.0, numpy, pillow, imageio-ffmpeg (see requirements.txt).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Point moviepy at the bundled ffmpeg from imageio-ffmpeg if system ffmpeg is absent.
try:
    import imageio_ffmpeg
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", imageio_ffmpeg.get_ffmpeg_exe())
    os.environ.setdefault("FFMPEG_BINARY", imageio_ffmpeg.get_ffmpeg_exe())
except Exception:  # pragma: no cover
    pass

from moviepy import (  # noqa: E402
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoClip,
    afx,
    vfx,
)

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #
ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
OUT_DIR = os.path.join(ROOT, "out")
FONT_DIR = "/mnt/skills/examples/canvas-design/canvas-fonts"

DURATION = 30.0          # exact master length (§13)
FRAME = (1080, 1920)     # 9:16 delivery (§13). --uhd doubles this.
OVERSAMPLE = 1.45        # render stills larger than frame so Ken Burns never sees an edge
XF = 0.45                # cross-dissolve length between scenes (§2)

# "Cozy-premium" brand palette (§5/§9) -----------------------------------------
CREAM_HI = (251, 246, 236)   # #FBF6EC
CREAM_LO = (243, 232, 214)   # #F3E8D6
INK = (90, 70, 50)           # #5A4632 warm brown, the text color on cream
ROSE = (200, 50, 75)         # tamed crochet red
PINK = (243, 195, 206)
GOLD = (244, 180, 0)
GOLD_DEEP = (200, 140, 20)
LEAF = (122, 139, 79)
PLUSH = (238, 224, 208)

# Font roles -> best local substitutes for the package's asks (§12) -----------
FONTS = {
    "serif":       os.path.join(FONT_DIR, "Lora-Bold.ttf"),          # ~ Playfair Display (logo/headings)
    "serif_reg":   os.path.join(FONT_DIR, "Lora-Regular.ttf"),
    "serif_it":    os.path.join(FONT_DIR, "Lora-Italic.ttf"),
    "sans":        os.path.join(FONT_DIR, "Outfit-Regular.ttf"),     # ~ Poppins (body/captions)
    "sans_bold":   os.path.join(FONT_DIR, "Outfit-Bold.ttf"),
    "script":      os.path.join(FONT_DIR, "NothingYouCouldDo-Regular.ttf"),  # ~ Dancing Script (handwritten)
}
# Graceful fallback if the font library is not present on another machine.
_FALLBACK = next((os.path.join(FONT_DIR, f) for f in os.listdir(FONT_DIR)), None) \
    if os.path.isdir(FONT_DIR) else None
for k, v in list(FONTS.items()):
    if not os.path.exists(v):
        FONTS[k] = _FALLBACK


# --------------------------------------------------------------------------- #
# Easing
# --------------------------------------------------------------------------- #
def smoothstep(p: float) -> float:
    """Ease-in-out. 'Linear moves read cheap and robotic.' (§8)"""
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) * (1 - p)


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def _cover_crop(img: Image.Image, tw: int, th: int) -> Image.Image:
    """CSS 'background-size: cover' -> resize keeping aspect then center-crop to tw x th."""
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = max(tw, int(round(sw * scale))), max(th, int(round(sh * scale)))
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _radial_glow(size, center, radius, color, strength=1.0):
    """Return an RGBA glow (used for warm backdrops / end card)."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / radius
    a = np.clip(1 - d, 0, 1) ** 2 * strength
    rgba = np.zeros((h, w, 4), np.float32)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = color
    rgba[..., 3] = a * 255
    return rgba.astype(np.uint8)


def gradient_cream(size, extra_glow=True):
    """Vertical cream->ivory gradient with a faint warm radial glow (§5)."""
    w, h = size
    t = np.linspace(0, 1, h)[:, None]
    top, bot = np.array(CREAM_HI, np.float32), np.array(CREAM_LO, np.float32)
    grad = (top[None, None, :] * (1 - t)[..., None] + bot[None, None, :] * t[..., None])
    grad = np.repeat(grad, w, axis=1)
    img = Image.fromarray(grad.astype(np.uint8), "RGB")
    if extra_glow:
        glow = Image.fromarray(_radial_glow(size, (w * 0.5, h * 0.42), h * 0.5,
                                            (255, 240, 210), 0.5), "RGBA")
        img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    return img


def make_placeholder(size, label, accent, sublabel=""):
    """On-brand stand-in card so a missing photo still animates nicely.

    Real photos always win -- this only fires for scenes whose image
    isn't in assets/. It is intentionally soft and cohesive, not a
    'missing image' error tile.
    """
    w, h = size
    img = gradient_cream(size).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # soft accent blob behind the label
    blob = Image.new("RGBA", size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(blob)
    r = int(min(w, h) * 0.30)
    cx, cy = w // 2, int(h * 0.44)
    bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (90,))
    blob = blob.filter(ImageFilter.GaussianBlur(min(w, h) * 0.05))
    img = Image.alpha_composite(img, blob)
    draw = ImageDraw.Draw(img)

    # script label
    fsize = int(w * 0.11)
    font = ImageFont.truetype(FONTS["script"], fsize)
    _center_text(draw, label, font, (cx, cy), INK)

    if sublabel:
        sf = ImageFont.truetype(FONTS["sans"], int(w * 0.032))
        _center_text(draw, sublabel, sf, (cx, int(h * 0.60)), (150, 130, 110))

    # tiny swap hint
    hf = ImageFont.truetype(FONTS["sans"], int(w * 0.024))
    _center_text(draw, "placeholder · drop a photo in assets/", hf,
                 (cx, int(h * 0.93)), (170, 155, 135))
    return np.array(img.convert("RGB"))


def _center_text(draw, text, font, center, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((center[0] - tw / 2 - bbox[0], center[1] - th / 2 - bbox[1]),
              text, font=font, fill=fill)


# --------------------------------------------------------------------------- #
# Asset resolution (maps package "Img N" roles -> files you drop in assets/)
# --------------------------------------------------------------------------- #
# For each scene role: candidate filenames (first found wins), placeholder label,
# and accent color. Names mirror the package's §0 asset map so they are obvious.
ASSET_SPEC = {
    "hook":       (["hook", "img13", "13", "macro", "sunflower"],      "yarn & stitch", GOLD),
    "bunny":      (["bunny", "img1", "img11", "1", "11"],              "bunny",         PINK),
    "roses":      (["roses", "rose_bouquet", "img16", "img3", "16"],   "red roses",     ROSE),
    "sunflower":  (["sunflower", "img14", "img13", "14"],              "sunflower",     GOLD),
    "bag":        (["bag", "tote", "img7", "7"],                       "sunflower bag", GOLD_DEEP),
    "key1":       (["key1", "keychain_rose", "img5", "5"],             "rose charm",    ROSE),
    "key2":       (["key2", "keychain_sunflower", "img6", "6"],        "sun charm",     GOLD),
    "key3":       (["key3", "keychain_cupcake", "img8", "8"],          "cupcake",       PINK),
    "key4":       (["key4", "keychain_bow", "img9", "9"],              "bow charm",     ROSE),
    "goose":      (["goose", "img18", "img17", "18"],                  "goose",         PLUSH),
    "turtles":    (["turtles", "img19", "19"],                         "turtles",       LEAF),
    "axolotl":    (["axolotl", "img15", "15"],                         "axolotl",       PINK),
    "bunny2":     (["bunny2", "bunny", "img12", "img1", "12"],         "bunny",         PINK),
    "vase":       (["vase", "sunflowers_vase", "img10", "10"],         "sunflowers",    GOLD),
}
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP")


def resolve_asset(role: str, size):
    """Return (np_rgb_array, is_real) for a role, at `size`, cover-cropped."""
    candidates, label, accent = ASSET_SPEC[role]
    for name in candidates:
        for ext in _IMG_EXTS:
            path = os.path.join(ASSETS, name + ext)
            if os.path.exists(path):
                img = Image.open(path).convert("RGB")
                return np.array(_cover_crop(img, *size)), True
    return make_placeholder(size, label, accent), False


# --------------------------------------------------------------------------- #
# Ken Burns clip (eased zoom + pan, never shows an edge)  -- §8
# --------------------------------------------------------------------------- #
def ken_burns(role, duration, zoom=(1.0, 1.06), pan=(0.0, 0.0), ease=smoothstep):
    """A still animated with a slow, eased camera move.

    zoom : (start, end) magnification (>=1). >1 pushes in, reverse to pull back.
    pan  : (x, y) in -1..1; drift across the available slack over the shot.
    """
    fw, fh = FRAME
    W, H = int(fw * OVERSAMPLE), int(fh * OVERSAMPLE)
    src, _real = resolve_asset(role, (W, H))
    z0, z1 = zoom

    def make_frame(t):
        p = ease(t / duration if duration else 0)
        z = z0 + (z1 - z0) * p
        win_w, win_h = W / z, H / z
        slack_x, slack_y = (W - win_w) / 2, (H - win_h) / 2
        cx = W / 2 + pan[0] * slack_x * (p - 0.5) * 2
        cy = H / 2 + pan[1] * slack_y * (p - 0.5) * 2
        left = int(round(min(max(cx - win_w / 2, 0), W - win_w)))
        top = int(round(min(max(cy - win_h / 2, 0), H - win_h)))
        crop = src[top:top + int(round(win_h)), left:left + int(round(win_w))]
        # BILINEAR is ~2x faster than LANCZOS and indistinguishable in motion video.
        return np.array(Image.fromarray(crop).resize((fw, fh), Image.BILINEAR))

    return VideoClip(make_frame, duration=duration).with_duration(duration)


# --------------------------------------------------------------------------- #
# Text overlays  -- §12
# --------------------------------------------------------------------------- #
def _txt(text, font_key, size_frac, color, stroke=None, stroke_w=0, box_w=0.86):
    fw, _ = FRAME
    return TextClip(
        font=FONTS[font_key],
        text=text,
        font_size=int(fw * size_frac),
        color=color,
        stroke_color=stroke,
        stroke_width=stroke_w,
        method="caption",
        size=(int(fw * box_w), None),
        text_align="center",
        interline=int(fw * 0.01),
    )


def title_overlay(text, font_key, size_frac, y_frac, start, dur,
                  color="#FFF7EC", shadow=True):
    """A fading, gently-rising scene title, with soft drop shadow for legibility."""
    fw, fh = FRAME
    clips = []
    fin, fout = 0.45, 0.35
    main = _txt(text, font_key, size_frac, color)
    y = int(fh * y_frac)
    if shadow:
        sh = (_txt(text, font_key, size_frac, "#3A2A1C")
              .with_position(("center", y + int(fw * 0.006)))
              .with_start(start).with_duration(dur)
              .with_opacity(0.5)
              .with_effects([vfx.CrossFadeIn(fin), vfx.CrossFadeOut(fout)]))
        clips.append(sh)
    main = (main.with_position(("center", y))
            .with_start(start).with_duration(dur)
            .with_effects([vfx.CrossFadeIn(fin), vfx.CrossFadeOut(fout),
                           vfx.Resize(lambda t: 1.0 + 0.02 * ease_out(min(t / fin, 1)))]))
    clips.append(main)
    return clips


# --------------------------------------------------------------------------- #
# SRT captions -> burned-in overlays  -- §4
# --------------------------------------------------------------------------- #
def _ts(s):
    h, m, rest = s.split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0


def parse_srt(path):
    blocks = re.split(r"\n\s*\n", open(path, encoding="utf-8").read().strip())
    cues = []
    for b in blocks:
        lines = [x for x in b.splitlines() if x.strip()]
        if len(lines) >= 2 and "-->" in lines[1]:
            a, bb = [x.strip() for x in lines[1].split("-->")]
            cues.append((_ts(a), _ts(bb), " ".join(lines[2:])))
    return cues


def caption_clips(srt_path):
    fw, fh = FRAME
    out = []
    for start, end, text in parse_srt(srt_path):
        dur = max(0.3, end - start)
        y = int(fh * 0.80)  # inside safe area, clear of IG's bottom UI (§8)
        shadow = (_txt(text, "sans", 0.046, "#241812", box_w=0.82)
                  .with_position(("center", y + int(fw * 0.005)))
                  .with_start(start).with_duration(dur).with_opacity(0.55)
                  .with_effects([vfx.CrossFadeIn(0.15), vfx.CrossFadeOut(0.15)]))
        main = (_txt(text, "sans_bold", 0.046, "#FFFFFF",
                     stroke="#2A1C12", stroke_w=int(fw * 0.004), box_w=0.82)
                .with_position(("center", y))
                .with_start(start).with_duration(dur)
                .with_effects([vfx.CrossFadeIn(0.15), vfx.CrossFadeOut(0.15)]))
        out += [shadow, main]
    return out


# --------------------------------------------------------------------------- #
# Multi-image scenes: keychain pops (§2/5), plush cuts, collection montage
# --------------------------------------------------------------------------- #
def pop_montage(roles, duration):
    """Keychains 'pop' in sequence with an overshoot ease over a soft bg (§2/5)."""
    fw, fh = FRAME
    bg = ImageClip(np.array(gradient_cream(FRAME))).with_duration(duration)
    layers = [bg]
    n = len(roles)
    cell = int(fw * 0.42)
    # arrange in a loose 2x2 grid, centered
    positions = [(0.28, 0.36), (0.72, 0.36), (0.30, 0.66), (0.70, 0.66)]
    for i, role in enumerate(roles[:4]):
        arr, _ = resolve_asset(role, (cell, cell))
        appear = i * (duration * 0.55 / max(n, 1)) + 0.15
        clip = (ImageClip(arr).with_start(appear)
                .with_duration(duration - appear)
                .with_position(("center", "center")))
        px, py = positions[i]
        clip = clip.with_position((int(fw * px - cell / 2), int(fh * py - cell / 2)))
        # overshoot pop
        clip = clip.with_effects([
            vfx.CrossFadeIn(0.12),
            vfx.Resize(lambda t: _overshoot(t)),
        ])
        layers.append(clip)
    return CompositeVideoClip(layers, size=FRAME).with_duration(duration)


def _overshoot(t):
    if t >= 0.5:
        return 1.0
    p = t / 0.5
    return 0.6 + 0.55 * p - 0.15 * (p * p)  # rises past 1 then settles ~1.0


def sequence_scene(roles, duration):
    """Quick warm cuts between plush friends, each with a gentle scale (§2/6)."""
    per = duration / len(roles)
    sub = []
    for i, role in enumerate(roles):
        z = (1.0, 1.05)
        c = ken_burns(role, per + XF, zoom=z, ease=ease_out).with_start(i * per)
        if i > 0:
            c = c.with_effects([vfx.CrossFadeIn(0.25)])
        sub.append(c)
    return CompositeVideoClip(sub, size=FRAME).with_duration(duration)


def collection_montage(roles, duration, texts):
    """Rapid dissolves + staggered one-word-per-beat text (§2/8)."""
    per = duration / len(roles)
    layers = []
    for i, role in enumerate(roles):
        c = ken_burns(role, per + XF, zoom=(1.02, 1.08), ease=smoothstep).with_start(i * per)
        if i > 0:
            c = c.with_effects([vfx.CrossFadeIn(0.3)])
        layers.append(c)
    comp = CompositeVideoClip(layers, size=FRAME).with_duration(duration)
    # staggered text beats
    txts = []
    for i, t in enumerate(texts):
        txts += title_overlay(t, "serif", 0.062, 0.40, i * per + 0.05, per + 0.2)
    return CompositeVideoClip([comp] + txts, size=FRAME).with_duration(duration)


# --------------------------------------------------------------------------- #
# End card  -- §2 / §12
# --------------------------------------------------------------------------- #
def end_card(duration):
    fw, fh = FRAME
    bg_img = gradient_cream(FRAME)
    # extra centered glow to make the logo pop
    glow = Image.fromarray(_radial_glow(FRAME, (fw * 0.5, fh * 0.42), fh * 0.42,
                                        (255, 244, 216), 0.6), "RGBA")
    bg_img = Image.alpha_composite(bg_img.convert("RGBA"), glow).convert("RGB")
    bg = ImageClip(np.array(bg_img)).with_duration(duration)

    layers = [bg]
    # script accent
    layers += title_overlay("handmade with love", "script", 0.075, 0.30,
                            0.15, duration - 0.15, color="#B24A5E", shadow=False)
    # logo: serif, ink/blur-ish reveal approximated with fade + settle scale
    logo = (_txt("THE WOOLLY AFFAIRS", "serif", 0.085, "#5A4632", box_w=0.92)
            .with_position(("center", int(fh * 0.42)))
            .with_start(0.35).with_duration(duration - 0.35)
            .with_effects([vfx.CrossFadeIn(0.6),
                           vfx.Resize(lambda t: 1.06 - 0.06 * ease_out(min(t / 0.8, 1)))]))
    layers.append(logo)
    # supporting lines, staggered (§2)
    layers += title_overlay("Handmade Gifts · Crochet Creations · Custom Orders",
                            "sans", 0.030, 0.56, 1.1, duration - 1.1,
                            color="#6B5540", shadow=False)
    layers += title_overlay("DM to order", "sans_bold", 0.036, 0.61, 1.35,
                            duration - 1.35, color="#5A4632", shadow=False)
    layers += title_overlay("Follow @thewoollyaffairs", "sans_bold", 0.040, 0.70,
                            1.6, duration - 1.6, color="#B24A5E", shadow=False)
    return CompositeVideoClip(layers, size=FRAME).with_duration(duration)


# --------------------------------------------------------------------------- #
# Timeline (from §2). Each entry drives one segment of the reel.
# --------------------------------------------------------------------------- #
@dataclass
class Scene:
    start: float
    end: float
    kind: str                       # 'kb' | 'pop' | 'seq' | 'collection' | 'endcard'
    role: str = ""
    roles: list = field(default_factory=list)
    zoom: tuple = (1.0, 1.06)
    pan: tuple = (0.0, 0.0)
    titles: list = field(default_factory=list)  # (text, font_key, size_frac, y_frac)


TIMELINE = [
    Scene(0.0, 3.0, "kb", role="hook", zoom=(1.18, 1.0),  # push then reveal (pull back)
          titles=[("Some gifts fade…", "serif_reg", 0.058, 0.40),
                  ("Handmade memories don't.", "serif", 0.064, 0.48)]),
    Scene(3.0, 6.0, "kb", role="bunny", zoom=(1.0, 1.05),
          titles=[("Handmade with love", "script", 0.085, 0.72)]),
    Scene(6.0, 9.0, "kb", role="roses", zoom=(1.02, 1.07),
          titles=[("Flowers that never wilt.", "serif", 0.062, 0.70)]),
    Scene(9.0, 12.0, "kb", role="sunflower", zoom=(1.0, 1.06),
          titles=[("Carry sunshine wherever you go.", "serif", 0.056, 0.70)]),
    Scene(12.0, 15.0, "kb", role="bag", zoom=(1.03, 1.03), pan=(0.0, -1.0),  # vertical pan
          titles=[("Designed for everyday happiness.", "serif", 0.052, 0.70)]),
    Scene(15.0, 18.0, "pop", roles=["key1", "key2", "key3", "key4"],
          titles=[("A little joy in every pocket.", "serif", 0.056, 0.80)]),
    Scene(18.0, 21.0, "seq", roles=["goose", "turtles", "axolotl"]),  # peak, faces carry it
    Scene(21.0, 23.5, "kb", role="bunny2", zoom=(1.0, 1.04),
          titles=[("The perfect gift.", "serif", 0.062, 0.72)]),
    Scene(23.5, 26.5, "collection", roles=["vase", "roses", "bag", "goose"],
          titles=[("Made with love.", "x", 0, 0),
                  ("Crafted by hand.", "x", 0, 0),
                  ("Created to last.", "x", 0, 0)]),  # texts handled inside montage
    Scene(26.5, 30.0, "endcard"),
]


# --------------------------------------------------------------------------- #
# Color grade + vignette + grain (final pass)  -- §9
# --------------------------------------------------------------------------- #
def build_grade(add_grain=True):
    fw, fh = FRAME
    # cache a warm vignette mask
    yy, xx = np.mgrid[0:fh, 0:fw]
    cx, cy = fw / 2, fh * 0.46
    d = np.sqrt(((xx - cx) / (fw * 0.72)) ** 2 + ((yy - cy) / (fh * 0.72)) ** 2)
    vig = np.clip(1.0 - 0.28 * np.clip(d - 0.35, 0, 1), 0.72, 1.0).astype(np.float32)
    vig = vig[..., None]

    # Pre-baked grain: cheap quarter-res textures cycled per frame instead of
    # drawing millions of fresh randoms every frame (that was a real cost).
    gh, gw = fh // 4, fw // 4
    grain_bank = [np.random.normal(0, 2.2, (gh, gw, 1)).astype(np.float32)
                  for _ in range(12)]
    counter = {"i": 0}

    def grade(frame):
        f = frame.astype(np.float32)
        # matte / lifted low-contrast base: pull toward mid, lift blacks (§9)
        f = f * 0.88 + 20.0
        # warm white balance: nudge R up, B down; small green lift
        f[..., 0] *= 1.055
        f[..., 1] *= 1.012
        f[..., 2] *= 0.955
        # tame reds a touch so crochet roses don't go electric (§9)
        red_dom = (f[..., 0] > f[..., 1] + 40) & (f[..., 0] > f[..., 2] + 40)
        f[..., 0][red_dom] *= 0.93
        # gentle highlight bloom: soft-light-ish lift on brights
        hi = np.clip((f.mean(axis=2, keepdims=True) - 180) / 75, 0, 1)
        f += hi * 12.0
        # vignette (warm, low strength)
        f *= vig
        if add_grain:
            g = grain_bank[counter["i"] % len(grain_bank)]
            counter["i"] += 1
            f += np.repeat(np.repeat(g, 4, axis=0), 4, axis=1)[:f.shape[0], :f.shape[1]]
        return np.clip(f, 0, 255).astype(np.uint8)

    return grade


# --------------------------------------------------------------------------- #
# Assemble
# --------------------------------------------------------------------------- #
def build_scene_clip(sc: Scene):
    dur = sc.end - sc.start
    tail = XF if sc.end < DURATION else 0.0  # extend into next scene for the dissolve
    d = dur + tail
    if sc.kind == "kb":
        base = ken_burns(sc.role, d, zoom=sc.zoom, pan=sc.pan)
        layers = [base]
        for (text, fk, sf, yf) in sc.titles:
            layers += title_overlay(text, fk, sf, yf, 0.35, dur - 0.2)
        clip = CompositeVideoClip(layers, size=FRAME).with_duration(d)
    elif sc.kind == "pop":
        base = pop_montage(sc.roles, d)
        layers = [base]
        for (text, fk, sf, yf) in sc.titles:
            layers += title_overlay(text, fk, sf, yf, 0.3, dur - 0.2)
        clip = CompositeVideoClip(layers, size=FRAME).with_duration(d)
    elif sc.kind == "seq":
        clip = sequence_scene(sc.roles, d)
    elif sc.kind == "collection":
        clip = collection_montage(sc.roles, d, [t[0] for t in sc.titles])
    elif sc.kind == "endcard":
        clip = end_card(d)
    else:
        raise ValueError(sc.kind)
    return clip.with_start(sc.start).with_duration(d)


def build_reel(fps, srt_path, add_grain=True):
    fw, fh = FRAME
    base_bg = ColorClip(FRAME, color=CREAM_LO).with_duration(DURATION)

    scene_clips = []
    for i, sc in enumerate(TIMELINE):
        c = build_scene_clip(sc)
        if i == 0:
            c = c.with_effects([vfx.FadeIn(0.3)])
        else:
            c = c.with_effects([vfx.CrossFadeIn(XF)])
        scene_clips.append(c)

    caps = caption_clips(srt_path)

    master = CompositeVideoClip([base_bg] + scene_clips + caps, size=FRAME)
    master = master.with_duration(DURATION)
    master = master.image_transform(build_grade(add_grain))
    return master


# --------------------------------------------------------------------------- #
# Audio (optional)  -- §10 / §11
# --------------------------------------------------------------------------- #
def attach_audio(clip, vo=None, music=None, sfx=None, music_gain=0.30):
    tracks = []
    if music and os.path.exists(music):
        m = AudioFileClip(music).with_effects([afx.MultiplyVolume(music_gain)])
        m = m.with_duration(min(m.duration, DURATION))
        tracks.append(m)
    if vo and os.path.exists(vo):
        tracks.append(AudioFileClip(vo).with_start(0.3))  # VO starts with first caption
    if sfx and os.path.exists(sfx):
        tracks.append(AudioFileClip(sfx).with_effects([afx.MultiplyVolume(0.4)]))
    if not tracks:
        return clip
    return clip.with_audio(CompositeAudioClip(tracks).with_duration(DURATION))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    global FRAME
    ap = argparse.ArgumentParser(description="Render the Woolly Affairs 30s reel.")
    ap.add_argument("--fps", type=int, default=30, help="frame rate (package uses 60)")
    ap.add_argument("--uhd", action="store_true", help="render 2160x3840 master")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "woolly_reel.mp4"))
    ap.add_argument("--srt", default=os.path.join(ASSETS, "captions.srt"))
    ap.add_argument("--vo", default=None, help="voice-over audio file to mux")
    ap.add_argument("--music", default=None, help="music bed (ducked under VO)")
    ap.add_argument("--sfx", default=None, help="sfx stem")
    ap.add_argument("--no-grain", action="store_true")
    ap.add_argument("--list-assets", action="store_true",
                    help="print the filename each scene looks for, then exit")
    args = ap.parse_args()

    if args.list_assets:
        print("Drop photos in assets/ using any of these basenames "
              "(first found wins; ext .jpg/.png/.webp):\n")
        for role, (cands, label, _) in ASSET_SPEC.items():
            print(f"  {role:10s} ({label:14s}) -> {', '.join(cands)}")
        print("\nScenes and the roles they use:")
        for sc in TIMELINE:
            r = sc.role or ", ".join(sc.roles) or "(end card)"
            print(f"  {sc.start:5.1f}-{sc.end:4.1f}s  {sc.kind:11s} {r}")
        return

    if args.uhd:
        FRAME = (2160, 3840)

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Rendering {FRAME[0]}x{FRAME[1]} @ {args.fps}fps  ->  {args.out}")
    reel = build_reel(args.fps, args.srt, add_grain=not args.no_grain)
    reel = attach_audio(reel, vo=args.vo, music=args.music, sfx=args.sfx)

    reel.write_videofile(
        args.out,
        fps=args.fps,
        codec="libx264",
        audio_codec="aac",
        audio_bitrate="320k",
        bitrate="28000k",              # high; IG re-encodes (§13)
        preset="veryfast",
        ffmpeg_params=["-pix_fmt", "yuv420p", "-profile:v", "high",
                       "-g", str(args.fps * 1)],  # ~1s keyframe interval
        threads=os.cpu_count() or 4,
    )
    print("Done:", args.out)


if __name__ == "__main__":
    main()
