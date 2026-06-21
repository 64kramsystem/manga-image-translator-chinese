"""Scene-description backends for translation context.

Given a comic page image and the running cast list so far, a backend returns two
things: a short prose SCENE description for the current page (characters + apparent
gender, who speaks to whom, tone, key on-panel action) and an updated CAST list of
the book's recurring characters — so a character whose gender is ambiguous on one
page gets pinned down once a clearer page appears. Both feed the translator; the cast
also carries forward to the next page (no separate pass). Three backends:

  claude  — `claude -p`, reads the image path via its file tool (Claude subscription)
  codex   — `codex exec -i IMG -o OUT` (ChatGPT/Codex subscription)
  qwen    — a local vision model via Ollama /api/chat (default qwen3.6:27b), free

claude/codex read the page file directly; qwen is sent a base64 copy downscaled to
1024px for speed. None of these translate — they only describe.
"""
import base64
import io
import json
import os
import re
import subprocess
import tempfile
import urllib.request

from PIL import Image

PROMPT = (
    "You are building translation context for ONE page of a comic. Produce TWO sections, each "
    "introduced by its exact header on its own line.\n\n"
    "SCENE:\n"
    "One short paragraph describing this page for a translator — the characters present and each "
    "one's apparent gender, who speaks to whom in each speech bubble, the emotional tone, and any "
    "key on-panel action or object the dialogue might refer to. Do NOT translate.\n\n"
    "CAST:\n"
    "The book's running list of recurring, identifiable characters, one per line as "
    "`Name or description — gender — one distinguishing detail`. Start from the existing list "
    "below; ADD any recurring character you can identify on this page, and if this page makes an "
    "earlier entry clearer (e.g. a character whose gender looked ambiguous), CORRECT that line. "
    "Omit one-off background figures. If nothing changes, repeat the list unchanged."
)
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.environ.get("SCANLATE_DESC_TIMEOUT", "300"))


def _claude(image_path, model, prompt):
    # --setting-sources "" keeps the user's global CLAUDE.md/settings out of the call
    # (OAuth subscription auth and the Read tool still work).
    cmd = ["claude", "--setting-sources", ""] + (["--model", model] if model else [])
    cmd += ["-p", f"{prompt}\n\nThe comic page image is the file at: {image_path}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError(f"claude describe failed: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def _codex(image_path, model, prompt):
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
        out = f.name
    try:
        cmd = ["codex", "exec", "-i", image_path, "-o", out] + (["-m", model] if model else [])
        cmd += [prompt]
        subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        return open(out).read().strip()
    finally:
        os.path.exists(out) and os.unlink(out)


def _qwen(image_path, model, prompt):
    im = Image.open(image_path).convert("RGB")
    s = 1024 / max(im.size)
    if s < 1:
        im = im.resize((int(im.size[0] * s), int(im.size[1] * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    req = {"model": model or "qwen3.6:27b", "stream": False, "think": False,
           "messages": [{"role": "user", "content": prompt, "images": [b64]}]}
    r = urllib.request.urlopen(
        urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(req).encode(),
                               headers={"Content-Type": "application/json"}), timeout=600)
    return json.load(r)["message"]["content"].strip()


BACKENDS = {"claude": _claude, "codex": _codex, "qwen": _qwen}


def describe(image_path, backend, model=None, cast=""):
    """Return (scene_description, updated_cast) for one page, given the cast so far."""
    seed = cast.strip() if cast and cast.strip() else "(none identified yet)"
    prompt = PROMPT + "\n\nExisting cast list so far:\n" + seed
    raw = BACKENDS[backend](image_path, model, prompt)
    return _split(raw, (cast or "").strip())


def _split(raw, prev_cast):
    """Split a labelled reply into (scene, updated_cast). If the CAST header is missing, keep the
    previous cast; a leading SCENE header is stripped from the description."""
    raw = (raw or "").strip()
    m = re.search(r"(?im)^[\s>*#-]*cast\s*:", raw) or re.search(r"(?i)\bcast\s*:", raw)
    if not m:
        return _scene(raw), prev_cast
    return _scene(raw[:m.start()]), (_clean(raw[m.end():]) or prev_cast)


def _scene(s):
    return _clean(re.sub(r"(?is)^[\s>*#-]*scene\s*:", "", s.strip()))


def _clean(s):
    return s.strip().strip("*#").strip()
