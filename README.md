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

Detection, OCR (48px), inpainting (lama_large) and English typesetting (manga2eng) are the
upstream defaults; `run.py` wires them to the `claude_cli` translator.
