# manga-image-translator-chinese

A fork of [manga-image-translator](https://github.com/zyddnys/manga-image-translator) for
end-to-end comic scanlation, adding:

- **`claude_cli` translator** — translates via the local `claude` CLI (Claude Code), drawing on
  a Claude subscription instead of a paid API key. A whole volume is translated in ONE
  conversation (`--session-id` then `--resume`), so each page has the context of every prior
  page. Tuned for Cantonese-flavoured Chinese. Select with `--translator claude_cli`; override
  the model with `SCANLATE_CLAUDE_MODEL`.
- **`scanlate/` harness** — batch-translates `.cbz`/`.epub` volumes (or image folders) and emits
  one **JPEG2000 PDF** per volume, resuming where it left off.

The translation engine, models, and all other options are upstream's; see the upstream README.

## Usage

Set up the engine per upstream (venv, models). The harness additionally needs `img2pdf`
(`pip install img2pdf`) and ImageMagick (`convert`) on PATH, and the `claude` CLI installed.

```sh
# one JP2 PDF per volume into OUT/, translating Chinese -> English
scanlate/run.py OUT/ vol1.cbz vol2.epub --target-lang ENG --quality 55
```

A **scene description** runs as its own per-volume conversation and feeds each page's context
to the translator (`--describe qwen|claude|codex|none`, default `qwen` — the local
`qwen3.6:35b-a3b` MoE, fast and free; `claude` uses a server-side session, `codex` is per-page).
It is computed only for pages that have text to translate.

Both conversations are seeded by the volume's **cast note** — `<out_dir>/<stem>.cast.txt`
(`name — gender — detail` lines), or `--notes FILE`, or the previous volume's note. Holding the
whole volume in one conversation keeps each character's gender/identity consistent; at the end the
describe conversation writes the volume's completed cast note, which seeds the next volume.

Detection, OCR (48px), inpainting (lama_large) and English typesetting (manga2eng) are the
upstream defaults; `run.py` wires them to the `claude_cli` translator.
