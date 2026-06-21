#!/usr/bin/env python3
"""Extract a comic volume's page images, in reading order, to a folder.

Handles .cbz (zip of images) and .epub (spine-ordered image refs). Pages are
written as NNNN.<ext> preserving the original image bytes (no transcode).
"""
import argparse
import os
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _cbz_pages(z):
    return sorted((n for n in z.namelist() if n.lower().endswith(IMG_EXT)), key=_natural_key)


def _epub_pages(z):
    """Image hrefs in spine reading order, parsed via the OPF."""
    container = z.read("META-INF/container.xml")
    opf_path = ET.fromstring(container).find(".//{*}rootfile").get("full-path")
    opf_dir = posixpath.dirname(opf_path)
    opf = ET.fromstring(z.read(opf_path))

    manifest = {item.get("id"): (item.get("href"), item.get("media-type") or "")
                for item in opf.find("{*}manifest")}

    def resolve(href):
        return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href

    names = set(z.namelist())
    pages = []
    for ref in opf.find("{*}spine"):
        item = manifest.get(ref.get("idref"))
        if not item:
            continue
        href, mtype = item
        target = resolve(href)
        if mtype.startswith("image/") or target.lower().endswith(IMG_EXT):
            pages.append(target)
        else:
            try:
                doc = z.read(target).decode("utf-8", "ignore")
            except KeyError:
                continue
            for m in re.finditer(r'(?:src|xlink:href|href)\s*=\s*["\']([^"\']+)["\']', doc):
                img = posixpath.normpath(posixpath.join(posixpath.dirname(target), m.group(1)))
                if img.lower().endswith(IMG_EXT) and img in names:
                    pages.append(img)
    seen, ordered = set(), []
    for p in pages:
        if p not in seen and p in names:
            seen.add(p)
            ordered.append(p)
    return ordered


def extract(volume, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(volume) as z:
        pages = (_epub_pages(z) if volume.lower().endswith(".epub") else _cbz_pages(z))
        if not pages:
            sys.exit(f"no page images found in {volume}")
        for i, name in enumerate(pages, 1):
            with open(os.path.join(out_dir, f"{i:04d}{os.path.splitext(name)[1].lower()}"), "wb") as f:
                f.write(z.read(name))
    return len(pages)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("volume")
    ap.add_argument("out_dir")
    a = ap.parse_args()
    print(f"{extract(a.volume, a.out_dir)} pages")
