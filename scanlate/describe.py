"""Scene-description backends for translation context.

Given a comic page image, returns a short prose description (characters + apparent
gender, who speaks to whom, tone, key on-panel action) to feed a translator as
context. Three backends:

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
import subprocess
import tempfile
import urllib.request

from PIL import Image

PROMPT = (
    "Describe this comic page to give a translator context — one short paragraph. Cover: the "
    "characters present and each one's apparent gender; for each speech bubble, who is speaking "
    "and to whom; the emotional tone; and any key on-panel action or object the dialogue might "
    "refer to. Do NOT translate — describe."
)
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.environ.get("SCANLATE_DESC_TIMEOUT", "300"))


def _claude(image_path, model):
    cmd = ["claude"] + (["--model", model] if model else [])
    cmd += ["-p", f"{PROMPT}\n\nThe comic page image is the file at: {image_path}"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    if p.returncode != 0:
        raise RuntimeError(f"claude describe failed: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def _codex(image_path, model):
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
        out = f.name
    try:
        cmd = ["codex", "exec", "-i", image_path, "-o", out] + (["-m", model] if model else [])
        cmd += [PROMPT]
        subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        return open(out).read().strip()
    finally:
        os.path.exists(out) and os.unlink(out)


def _qwen(image_path, model):
    im = Image.open(image_path).convert("RGB")
    s = 1024 / max(im.size)
    if s < 1:
        im = im.resize((int(im.size[0] * s), int(im.size[1] * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    req = {"model": model or "qwen3.6:27b", "stream": False, "think": False,
           "messages": [{"role": "user", "content": PROMPT, "images": [b64]}]}
    r = urllib.request.urlopen(
        urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(req).encode(),
                               headers={"Content-Type": "application/json"}), timeout=600)
    return json.load(r)["message"]["content"].strip()


BACKENDS = {"claude": _claude, "codex": _codex, "qwen": _qwen}


def describe(image_path, backend, model=None):
    return BACKENDS[backend](image_path, model)
