# manga-image-translator-chinese

A fork of [manga-image-translator](https://github.com/zyddnys/manga-image-translator) for
end-to-end comic scanlation, adding:

- **Two pluggable translators** — both translate a whole volume in ONE conversation, so each
  page has the context of every prior page (consistent names, voices, phrasings). Tuned for
  Cantonese-flavoured Chinese.
  - **`claude_cli`** (default) — translates via the local `claude` CLI (Claude Code), drawing on a
    Claude subscription instead of a paid API key (`--session-id` then `--resume`). Highest quality:
    most faithful, and resolves character names from the scene context. Override the model with
    `SCANLATE_CLAUDE_MODEL`.
  - **`heretic`** — a local abliterated ("Heretic") **Qwen3.6-27B** via Ollama: free, offline, no
    rate limit, and uncensored so adult/violent dialogue is translated faithfully rather than
    silently softened. Override with `SCANLATE_HERETIC_MODEL` / `SCANLATE_HERETIC_NUM_CTX`.
- **`scanlate/` harness** — batch-translates `.cbz`/`.epub` volumes (or image folders) and emits
  one **JPEG2000 PDF** per volume, resuming where it left off. Pick the translator with
  `--translator claude_cli|heretic`.

The translation engine, models, and all other options are upstream's; see the upstream README.

## Usage

Set up the engine per upstream (venv, models). The harness additionally needs `img2pdf`
(`pip install img2pdf`) and ImageMagick (`convert`) on PATH; for translation, the `claude` CLI
installed (default) or the Ollama daemon with the Heretic model pulled (`--translator heretic`).

```sh
# one JP2 PDF per volume into OUT/, translating Chinese -> English
scanlate/run.py OUT/ vol1.cbz vol2.epub --target-lang ENG --quality 40
```

A **scene description** runs as its own per-volume conversation and feeds each page's context
to the translator (`--describe qwen|claude|codex|none`, default `qwen` — the local dense
`qwen3.6:27b`, which reliably holds the seeded cast across pages where the faster `35b-a3b` MoE
drops absent characters; override with `--describe-model`. `claude` uses a server-side session,
`codex` is per-page). Each page is described with the previous page's image too, for visual
character continuity. It is computed only for pages that have text to translate.

Both conversations are seeded by the volume's **cast note** — `<out_dir>/<stem>.cast.txt`
(`name — gender — detail` lines), or `--notes FILE`, or the previous volume's note. Holding the
whole volume in one conversation keeps each character's gender/identity consistent; at the end the
describe conversation writes the volume's completed cast note, which seeds the next volume.

Detection, OCR (48px), inpainting (lama_large) and English typesetting (manga2eng) are the
upstream defaults; `run.py` wires them to the `claude_cli` translator.
