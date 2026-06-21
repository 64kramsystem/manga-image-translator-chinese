#!/usr/bin/env python3
"""Batch scanlation: translate comic volumes end to end with the claude_cli
translator, emitting one JPEG2000 PDF per volume.

For each input (a .cbz/.epub file or a folder of page images) it: extracts pages
in reading order, runs the MIT pipeline (detection -> OCR -> claude_cli
translation -> inpaint -> manga2eng render), and assembles a JP2 PDF. Pages whose
rendered output already exists are skipped, so an interrupted run resumes cheaply.

Run under MIT's venv with `img2pdf` installed and ImageMagick on PATH.

  scanlate/run.py OUT_DIR VOLUME [VOLUME ...] [--describe claude|codex|qwen|none] [--quality 55]

Stage 1 is an optional scene description (--describe backend) fed to the translator
as context; the same pass maintains a running per-volume cast list (work/<stem>/cast.txt,
seeded by --notes) so character genders/pronouns stay consistent. Stage 2 is the
claude_cli translation.
"""
import argparse
import asyncio
import functools
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
from describe import describe  # noqa: E402

FONT = os.path.join(REPO, "fonts", "anime_ace_3.ttf")


def build_config(target_lang):
    return Config(
        translator=TranslatorConfig(
            translator=Translator.claude_cli,
            target_lang=target_lang,
            enable_post_translation_check=False,
        ),
        render=RenderConfig(renderer=Renderer.manga2Eng),
        detector=DetectorConfig(detector=Detector.default),
        ocr=OcrConfig(ocr=Ocr.ocr48px),
        inpainter=InpainterConfig(inpainter=Inpainter.lama_large),
    )


async def scanlate_volume(mt, cfg, volume, work_dir, out_dir, quality,
                          translator, describe_backend, describe_model, seed):
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

    # Running cast notes: resume from the on-disk state if present, else from the seed.
    # The describe pass extends them page by page (see describe() -> updated_cast).
    cast_path = os.path.join(work_dir, stem, "cast.txt")
    if os.path.exists(cast_path):
        translator.cast_notes = open(cast_path).read().strip() or None
    else:
        translator.cast_notes = seed.strip() or None
    translator.session_id = None   # one fresh claude conversation per volume

    for i, fn in enumerate(pages, 1):
        out_png = os.path.join(rendered, f"{os.path.splitext(fn)[0]}.png")
        if os.path.exists(out_png):
            continue
        page_path = os.path.join(pages_dir, fn)
        if describe_backend != "none":
            translator.scene_provider = functools.partial(
                describe, page_path, describe_backend, describe_model,
                translator.cast_notes or "")
        ctx = await mt.translate(Image.open(page_path).convert("RGB"), cfg, skip_context_save=True)
        ctx.result.save(out_png)
        if translator.last_description:
            with open(os.path.join(rendered, f"{os.path.splitext(fn)[0]}.desc.txt"), "w") as f:
                f.write(translator.last_description + "\n")
        if describe_backend != "none" and translator.cast_notes:
            with open(cast_path, "w") as f:
                f.write(translator.cast_notes.strip() + "\n")
        print(f"[{stem}] {i}/{len(pages)}  ({len(ctx.text_regions or [])} regions)")

    out_pdf = os.path.join(out_dir, f"{stem}.pdf")
    build_pdf(rendered, out_pdf, quality)
    print(f"[{stem}] -> {out_pdf}")


async def main(a):
    if a.model:
        os.environ["SCANLATE_CLAUDE_MODEL"] = a.model
    params = {"use_gpu": True, "font_path": FONT, "verbose": False, "kernel_size": 3}
    if a.model_dir:
        params["model_dir"] = a.model_dir
    mt = MangaTranslator(params)
    cfg = build_config(a.target_lang)
    translator = get_translator(Translator.claude_cli)  # shared cached instance
    seed = open(a.notes).read() if a.notes else ""
    os.makedirs(a.out_dir, exist_ok=True)
    for vol in a.volumes:
        await scanlate_volume(mt, cfg, vol, a.work_dir, a.out_dir, a.quality,
                              translator, a.describe, a.describe_model, seed)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir")
    ap.add_argument("volumes", nargs="+", help=".cbz/.epub files or page-image folders")
    ap.add_argument("--target-lang", default="ENG")
    ap.add_argument("--model", default=None, help="claude --model (default: CLI default)")
    ap.add_argument("--quality", type=int, default=55, help="JP2 quality (ImageMagick scale)")
    ap.add_argument("--work-dir", default="scanlate_work")
    ap.add_argument("--model-dir", default=None, help="reuse an existing MIT models/ dir")
    ap.add_argument("--describe", choices=["none", "claude", "codex", "qwen"], default="claude",
                    help="scene-description backend fed to the translator as context")
    ap.add_argument("--describe-model", default=None, help="model for the describe backend")
    ap.add_argument("--notes", default=None,
                    help="seed file of recurring-character facts; the describe pass extends it per page")
    asyncio.run(main(ap.parse_args()))
