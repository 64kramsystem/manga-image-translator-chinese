# manga-image-translator-chinese

A fork of [manga-image-translator](https://github.com/zyddnys/manga-image-translator) for
end-to-end comic scanlation, adding:

- **`claude_cli` translator** — translates via the local `claude` CLI (Claude Code) in print
  mode, drawing on a Claude subscription instead of a paid API key. It translates a whole page
  at once for context and is tuned for Cantonese-flavoured Chinese. Select with
  `--translator claude_cli`; override the model with `SCANLATE_CLAUDE_MODEL`.
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

Optionally a per-page **scene description** is generated and fed to the translator as context
(`--describe claude|codex|qwen|none`, default `claude`; `codex` uses the `codex` CLI, `qwen` a
local Ollama vision model). It is computed only for pages that have text to translate.

The describe pass also maintains a **running cast list** for the volume (`work/<stem>/cast.txt`):
each page, the same vision call updates a short roster of recurring characters (name, gender, a
distinguishing detail), which is fed back into both stages so genders and pronouns stay consistent
even though pages are processed independently — no extra pass. `--notes FILE` seeds it with facts
you already know (e.g. *"Ah-Chung is male, long hair"*); the pass extends and corrects it as
clearer pages appear.

Detection, OCR (48px), inpainting (lama_large) and English typesetting (manga2eng) are the
upstream defaults; `run.py` wires them to the `claude_cli` translator.
