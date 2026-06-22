#!/usr/bin/env python3
"""Assemble rendered page PNGs into a single PDF whose pages are JPEG2000.

Each PNG is encoded to JP2 with ImageMagick (`-quality Q`), then embedded
losslessly into the PDF via img2pdf (JPXDecode — no re-encoding). Requires
`img2pdf` (pip) and ImageMagick `convert` on PATH.

ImageMagick's JP2 `-quality` is NOT a JPEG-style percentage: it is near-lossless
above ~50 and only compresses meaningfully below ~40. q40 keeps text/line art
visually intact while staying near the source size.

With `originals_dir`, every page but the cover is paired side by side — the
original page on the left, the scanlated page on the right, split by a 2px
black rule — so a reader can check the translation against the source.

  to_pdf.py PNG_DIR OUT.pdf [--quality 40] [--originals ORIG_DIR]
"""
import argparse
import glob
import os
import subprocess
import tempfile

import img2pdf


def _original_for(originals_dir, png):
    stem = os.path.splitext(os.path.basename(png))[0]
    return next(iter(glob.glob(os.path.join(originals_dir, stem + ".*"))), None)


def _encode(scanlated, original, out_jp2, quality):
    """Encode one PDF page to JP2: the scanlated page alone, or original | 2px rule | scanlated."""
    if original is None:
        cmd = ["convert", scanlated]
    else:
        h = subprocess.run(["identify", "-format", "%h", scanlated],
                           capture_output=True, text=True, check=True).stdout.strip()
        cmd = ["convert", "(", original, "-resize", "x" + h, ")",
               "(", "-size", "2x" + h, "xc:black", ")", scanlated, "+append"]
    subprocess.run(cmd + ["-quality", str(quality), out_jp2], check=True)


def build_pdf(png_dir, out_pdf, quality=40, originals_dir=None):
    pngs = sorted(glob.glob(os.path.join(png_dir, "*.png")))
    if not pngs:
        raise SystemExit(f"no PNGs in {png_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        jp2s = []
        for i, png in enumerate(pngs):
            jp2 = os.path.join(tmp, os.path.splitext(os.path.basename(png))[0] + ".jp2")
            # i == 0 is the cover: kept as a single page even when pairing is on.
            original = _original_for(originals_dir, png) if originals_dir and i > 0 else None
            _encode(png, original, jp2, quality)
            jp2s.append(jp2)
        os.makedirs(os.path.dirname(os.path.abspath(out_pdf)), exist_ok=True)
        with open(out_pdf, "wb") as f:
            f.write(img2pdf.convert(jp2s))
    return len(pngs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("png_dir")
    ap.add_argument("out_pdf")
    ap.add_argument("--quality", type=int, default=40)
    ap.add_argument("--originals", default=None,
                    help="pair each page (cover excepted) beside its original from this dir")
    a = ap.parse_args()
    n = build_pdf(a.png_dir, a.out_pdf, a.quality, a.originals)
    print(f"{n} pages -> {a.out_pdf} ({os.path.getsize(a.out_pdf)/1e6:.1f} MB, JP2 q{a.quality})")
