#!/usr/bin/env python3
"""Batch scanlation: translate comic volumes end to end, emitting one JPEG2000 PDF
per volume.

For each input (a .cbz/.epub file or a folder of page images) it: extracts pages
in reading order, runs the MIT pipeline (detection -> OCR -> translation -> inpaint
-> manga2eng render), and assembles a JP2 PDF. Pages whose rendered output already
exists are skipped, so an interrupted run resumes cheaply.

The translation stage is pluggable via --translator: `claude_cli` (default, the Claude
subscription via the `claude` CLI — highest quality) or `heretic` (local abliterated
Qwen3.6-27B via Ollama — free, offline and uncensored). Both translate a whole volume
in one conversation seeded by its cast note.

Run under MIT's venv with `img2pdf` installed and ImageMagick on PATH.

  scanlate/run.py OUT_DIR VOLUME [VOLUME ...] [--translator claude_cli|heretic] [--describe qwen|claude|codex|none] [--quality 55]

Both scene description and translation run as one conversation per volume, seeded by
the volume's cast note (<out_dir>/<stem>.cast.txt, or --notes / the previous volume).
The describe conversation (default: local dense qwen) feeds each page's context to the
translation conversation; at the volume's end it writes the completed cast note, which
seeds the next volume.
"""
import argparse
import asyncio
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)   # manga_translator
sys.path.insert(0, HERE)   # extract, to_pdf

from manga_translator import MangaTranslator, Config  # noqa: E402
from manga_translator.config import (  # noqa: E402
    RenderConfig, TranslatorConfig, DetectorConfig, OcrConfig, InpainterConfig,
    Renderer, Translator, Ocr, Detector, Inpainter,
)
from manga_translator.translators import get_translator  # noqa: E402
from extract import extract, IMG_EXT  # noqa: E402
from to_pdf import build_pdf  # noqa: E402
from describe import Describer  # noqa: E402

FONT = os.path.join(REPO, "fonts", "anime_ace_3.ttf")


def build_config(target_lang, translator):
    return Config(
        translator=TranslatorConfig(
            translator=translator,
            target_lang=target_lang,
            enable_post_translation_check=False,
        ),
        render=RenderConfig(renderer=Renderer.manga2Eng),
        detector=DetectorConfig(detector=Detector.default),
        ocr=OcrConfig(ocr=Ocr.ocr48px),
        inpainter=InpainterConfig(inpainter=Inpainter.lama_large),
    )


async def scanlate_volume(mt, cfg, volume, work_dir, out_dir, quality,
                          translator, describe_backend, describe_model, cast_seed):
    stem = os.path.splitext(os.path.basename(volume.rstrip("/")))[0]
    pages_dir = os.path.join(work_dir, stem, "pages")
    rendered = os.path.join(work_dir, stem, "rendered")
    os.makedirs(rendered, exist_ok=True)

    if os.path.isdir(volume):
        pages_dir = volume
    elif not os.path.isdir(pages_dir) or not os.listdir(pages_dir):
        print(f"[{stem}] extracting…")
        extract(volume, pages_dir)

    pages = sorted(f for f in os.listdir(pages_dir) if f.lower().endswith(IMG_EXT))
    print(f"[{stem}] {len(pages)} pages")

    # Cast note: the volume's own file (if present, e.g. a resume or a hand-written
    # seed) wins over the cast inherited from the previous volume. It seeds both the
    # describe conversation and the translation conversation's opening turn.
    cast_path = os.path.join(out_dir, f"{stem}.cast.txt")
    seed = open(cast_path).read().strip() if os.path.exists(cast_path) else (cast_seed or "").strip()
    translator.start_volume(seed)   # one fresh translation conversation per volume
    describer = Describer(describe_backend, describe_model, seed) if describe_backend != "none" else None

    for i, fn in enumerate(pages, 1):
        out_png = os.path.join(rendered, f"{os.path.splitext(fn)[0]}.png")
        if os.path.exists(out_png):
            continue
        page_path = os.path.join(pages_dir, fn)
        if describer is not None:
            translator.scene_provider = lambda p=page_path: describer.describe(p)
        ctx = await mt.translate(Image.open(page_path).convert("RGB"), cfg, skip_context_save=True)
        ctx.result.save(out_png)
        if translator.last_description:
            with open(os.path.join(rendered, f"{os.path.splitext(fn)[0]}.desc.txt"), "w") as f:
                f.write(translator.last_description + "\n")
            if describer is not None and describer.cast:  # cast was just updated this page
                with open(cast_path, "w") as f:
                    f.write(describer.cast.strip() + "\n")
        print(f"[{stem}] {i}/{len(pages)}  ({len(ctx.text_regions or [])} regions)")

    # The cast note (seeds the next volume) was kept current page by page above.
    completed = describer.cast if describer is not None else seed

    out_pdf = os.path.join(out_dir, f"{stem}.pdf")
    build_pdf(rendered, out_pdf, quality)
    print(f"[{stem}] -> {out_pdf}")
    return completed


async def main(a):
    if a.model:
        os.environ["SCANLATE_CLAUDE_MODEL"] = a.model
    params = {"use_gpu": True, "font_path": FONT, "verbose": False, "kernel_size": 3}
    if a.model_dir:
        params["model_dir"] = a.model_dir
    mt = MangaTranslator(params)
    tkey = Translator(a.translator)
    cfg = build_config(a.target_lang, tkey)
    translator = get_translator(tkey)  # shared cached instance
    cast_seed = open(a.notes).read() if a.notes else ""
    describe_model = a.describe_model or ("qwen3.6:27b" if a.describe == "qwen" else None)
    os.makedirs(a.out_dir, exist_ok=True)
    for vol in a.volumes:
        # Each volume's completed cast note seeds the next.
        cast_seed = await scanlate_volume(mt, cfg, vol, a.work_dir, a.out_dir, a.quality,
                                          translator, a.describe, describe_model, cast_seed) or cast_seed


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir")
    ap.add_argument("volumes", nargs="+", help=".cbz/.epub files or page-image folders")
    ap.add_argument("--target-lang", default="ENG")
    ap.add_argument("--translator", choices=["claude_cli", "heretic"], default="claude_cli",
                    help="translation backend (default: claude_cli; heretic = local abliterated Qwen3.6-27B)")
    ap.add_argument("--model", default=None, help="claude --model (claude_cli only; default: CLI default)")
    ap.add_argument("--quality", type=int, default=55, help="JP2 quality (ImageMagick scale)")
    ap.add_argument("--work-dir", default="scanlate_work")
    ap.add_argument("--model-dir", default=None, help="reuse an existing MIT models/ dir")
    ap.add_argument("--describe", choices=["none", "claude", "codex", "qwen"], default="qwen",
                    help="scene-description backend fed to the translator as context (default: qwen MoE)")
    ap.add_argument("--describe-model", default=None, help="model for the describe backend")
    ap.add_argument("--notes", default=None,
                    help="seed file of recurring-character facts; the describe pass extends it per page")
    asyncio.run(main(ap.parse_args()))
