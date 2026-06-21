#!/usr/bin/env python3
"""Assemble rendered page PNGs into a single PDF whose pages are JPEG2000.

Each PNG is encoded to JP2 with ImageMagick (`-quality Q`), then embedded
losslessly into the PDF via img2pdf (JPXDecode — no re-encoding). Requires
`img2pdf` (pip) and ImageMagick `convert` on PATH.

  to_pdf.py PNG_DIR OUT.pdf [--quality 55]
"""
import argparse
import glob
import os
import subprocess
import tempfile

import img2pdf


def build_pdf(png_dir, out_pdf, quality=55):
    pngs = sorted(glob.glob(os.path.join(png_dir, "*.png")))
    if not pngs:
        raise SystemExit(f"no PNGs in {png_dir}")
    with tempfile.TemporaryDirectory() as tmp:
        jp2s = []
        for png in pngs:
            jp2 = os.path.join(tmp, os.path.splitext(os.path.basename(png))[0] + ".jp2")
            subprocess.run(["convert", png, "-quality", str(quality), jp2], check=True)
            jp2s.append(jp2)
        os.makedirs(os.path.dirname(os.path.abspath(out_pdf)), exist_ok=True)
        with open(out_pdf, "wb") as f:
            f.write(img2pdf.convert(jp2s))
    return len(pngs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("png_dir")
    ap.add_argument("out_pdf")
    ap.add_argument("--quality", type=int, default=55)
    a = ap.parse_args()
    n = build_pdf(a.png_dir, a.out_pdf, a.quality)
    print(f"{n} pages -> {a.out_pdf} ({os.path.getsize(a.out_pdf)/1e6:.1f} MB, JP2 q{a.quality})")
