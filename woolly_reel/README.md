# The Woolly Affairs — 30s Reel Renderer

Code that turns the production package (`production_package.md`) and the
caption file (`assets/captions.srt`) into a finished vertical MP4 —
`make_reel.py` implements the §2 timeline, motion, grade, captions and end
card described in the package.

## What it does

- **Timeline (§2):** hook + 8 scenes + end card, at the exact start times.
- **Motion (§8):** eased Ken Burns push/pull/pan on every still — no linear moves.
- **Overlays (§12):** animated scene titles using local substitutes for the
  package's fonts — **Lora** (≈ Playfair, serif/logo), **Outfit** (≈ Poppins,
  body/captions), **NothingYouCouldDo** (≈ Dancing Script, handwritten accent).
- **Captions (§4):** VO subtitles parsed from the `.srt`, burned in bottom-center
  inside the safe area with a soft shadow + stroke.
- **Transitions (§2):** cross-dissolves; keychain "pop" montage; plush quick-cuts;
  collection montage with staggered one-line-per-beat text.
- **Grade (§9):** the "cozy-premium" look — matte lifted blacks, warm balance,
  tamed reds, boosted golds, highlight bloom, warm vignette, fine grain.
- **End card (§2):** serif logo reveal + staggered CTA lines + script accent.
- **Audio (§10/§11):** optional VO / music / SFX muxing, music ducked under VO.

## Install

```bash
pip install -r requirements.txt
```

`imageio-ffmpeg` ships its own ffmpeg, so you don't need a system install.

## Add your photos

The package's images aren't bundled, so any scene without a photo renders an
on-brand placeholder card and the reel still plays end-to-end. To finalize,
drop your real photos into `assets/` using the basenames below (first match
wins; `.jpg` / `.png` / `.webp` all work). See the full map any time with:

```bash
python make_reel.py --list-assets
```

| Scene | Role | Drop a file named (any of) |
|-------|------|----------------------------|
| Hook (macro) | `hook` | `hook`, `img13`, `sunflower` |
| 1 · Bunny | `bunny` | `bunny`, `img1`, `img11` |
| 2 · Roses | `roses` | `roses`, `img16`, `img3` |
| 3 · Sunflower | `sunflower` | `sunflower`, `img14` |
| 4 · Sunflower bag | `bag` | `bag`, `tote`, `img7` |
| 5 · Keychains | `key1..key4` | `key1`/`img5`, `key2`/`img6`, `key3`/`img8`, `key4`/`img9` |
| 6 · Plush | `goose`,`turtles`,`axolotl` | `img18`, `img19`, `img15` |
| 7 · Bunny return | `bunny2` | `bunny2`, `img12`, `bunny` |
| 8 · Collection | `vase`,`roses`,`bag`,`goose` | `img10`, … |

> Tip from the package (§0): for busy stall backgrounds (roses, vase, keychains),
> cut the product out onto a clean cream backdrop before dropping it in — the
> renderer cover-crops but doesn't remove backgrounds.

## Render

```bash
python make_reel.py                       # 1080x1920 @30fps -> out/woolly_reel.mp4
python make_reel.py --fps 60              # 60fps (package default, buttery motion)
python make_reel.py --uhd --fps 60        # 2160x3840 master (survives IG recompress)
python make_reel.py --no-grain            # disable film grain
```

## Add audio

The package can't ship audio; generate the VO (ElevenLabs / Edge Neural, §3)
and pick music (§10), then mux:

```bash
python make_reel.py --fps 60 \
  --vo vo.wav \
  --music music.mp3        # auto-ducked to ~30% under the VO
```

## Export settings

Defaults match §13: H.264 / yuv420p, ~28 Mbps, AAC 320k, ~1s keyframe interval.
IG re-encodes reels, so the high bitrate (and optional 4K master) is deliberate.

## Tweaking

Everything is data-driven. The `TIMELINE` list near the bottom of
`make_reel.py` is the single source of truth for scene order, durations,
zoom/pan and on-screen text — edit it to re-cut (e.g. the package's optional
"6 hero scenes, ~4s each" slower version). Palette, fonts and grade constants
sit at the top of the file.
