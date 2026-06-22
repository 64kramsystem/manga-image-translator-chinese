"""Per-volume scene description in ONE running conversation.

A Describer narrates a volume's pages — one per turn — to give the translator
context, and maintains a running CAST that it updates every page, so a character's
identity/gender stays consistent and newly-introduced characters are pinned before
their page descriptions scroll out of the window. It is seeded with the volume's cast
note; the cast is kept current page by page and persisted as the volume's note (which
seeds the next volume). Backends:

  qwen   — local Ollama vision model (default qwen3.6:35b-a3b); scene history kept
           client-side, the cast pinned in the (regenerated) system message
  claude — `claude` CLI, server-side session via --session-id/--resume
  codex  — `codex` CLI, stateless per page

It only describes — never translates.
"""
import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

from PIL import Image

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.environ.get("SCANLATE_DESC_TIMEOUT", "300"))
CLAUDE_BIN = shutil.which("claude") or "claude"
# qwen runs at a fixed context; set it explicitly rather than trust Ollama's default.
NUM_CTX = int(os.environ.get("SCANLATE_DESC_NUM_CTX", "65536"))
# Recent page descriptions kept in the qwen conversation (the cast is pinned separately,
# so trimming old scenes is safe — identity rides on the always-present cast).
KEEP_PAGES = int(os.environ.get("SCANLATE_DESC_KEEP_PAGES", "40"))

ROLE = (
    "You are describing a Hong Kong manhua page by page to give a translator context. You keep a "
    "running CAST of recurring characters and use it to keep each one's gender/identity consistent "
    "— trust the cast over a single page's ambiguous art. For each page reply in two labelled "
    "sections:\n"
    "SCENE: one short paragraph — who is present (by cast name + gender), who speaks to whom in "
    "each bubble, the emotional tone, and any key on-panel action or object the dialogue refers to.\n"
    "CAST: the full updated roster, one per line as `Name — gender — one distinguishing detail`; "
    "add or correct anyone this page reveals and keep the rest. Do NOT translate."
)
PAGE_ASK = "Describe this page."


def _cast_block(cast):
    return "\n\nCurrent cast:\n" + (cast if cast else "(none yet — build it as you go)")


def _b64(image_path):
    im = Image.open(image_path).convert("RGB")
    s = 1024 / max(im.size)
    if s < 1:
        im = im.resize((int(im.size[0] * s), int(im.size[1] * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def _clean(s):
    return s.strip().strip("*#").strip()


def _scene(s):
    return _clean(re.sub(r"(?is)^[\s>*#-]*scene\s*:", "", s.strip()))


def _split(raw, prev_cast):
    """Split a labelled reply into (scene, updated_cast); keep the previous cast if no CAST header."""
    raw = (raw or "").strip()
    m = re.search(r"(?im)^[\s>*#-]*cast\s*:", raw) or re.search(r"(?i)\bcast\s*:", raw)
    if not m:
        return _scene(raw), prev_cast
    return _scene(raw[:m.start()]), (_clean(raw[m.end():]) or prev_cast)


class Describer:
    """One scene-description conversation for a whole volume, with a running cast."""

    def __init__(self, backend, model=None, cast_seed=""):
        self.backend = backend
        self.model = model
        self.cast = (cast_seed or "").strip()   # running roster, updated every page
        self.scenes = []                          # qwen: rolling scene-only history
        self.session_id = None                    # claude: server-side session id

    def describe(self, image_path):
        return getattr(self, "_d_" + self.backend)(image_path)

    # ---- qwen: Ollama, client-side history (scenes only); cast pinned in the system message ----
    def _ollama(self, messages):
        req = {"model": self.model or "qwen3.6:35b-a3b", "stream": False, "think": False,
               "options": {"num_ctx": NUM_CTX}, "messages": messages}
        r = urllib.request.urlopen(urllib.request.Request(
            OLLAMA + "/api/chat", data=json.dumps(req).encode(),
            headers={"Content-Type": "application/json"}), timeout=600)
        return json.load(r)["message"]["content"].strip()

    def _d_qwen(self, image_path):
        system = {"role": "system", "content": ROLE + _cast_block(self.cast)}
        raw = self._ollama([system] + self.scenes
                           + [{"role": "user", "content": PAGE_ASK, "images": [_b64(image_path)]}])
        scene, self.cast = _split(raw, self.cast)
        # Keep scenes only (no image, no cast) in a bounded recent window.
        self.scenes += [{"role": "user", "content": PAGE_ASK}, {"role": "assistant", "content": scene}]
        if len(self.scenes) > 2 * KEEP_PAGES:
            self.scenes = self.scenes[-2 * KEEP_PAGES:]
        return scene

    # ---- claude: server-side session ----
    def _claude(self, session_args, prompt):
        cmd = [CLAUDE_BIN, "--setting-sources", ""]
        if self.model:
            cmd += ["--model", self.model]
        cmd += session_args + ["-p", prompt]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        if p.returncode != 0:
            raise RuntimeError(f"claude describe failed: {p.stderr.strip()[:200]}")
        return p.stdout.strip()

    def _d_claude(self, image_path):
        if self.session_id is None:
            self.session_id = str(uuid.uuid4())
            raw = self._claude(["--session-id", self.session_id], ROLE + _cast_block(self.cast)
                               + f"\n\n{PAGE_ASK}\nThe page image is the file at: {image_path}")
        else:
            raw = self._claude(["--resume", self.session_id],
                              f"Next page.\n{PAGE_ASK}\nThe image is the file at: {image_path}")
        scene, self.cast = _split(raw, self.cast)
        return scene

    # ---- codex: stateless per page ----
    def _d_codex(self, image_path):
        with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
            out = f.name
        try:
            cmd = ["codex", "exec", "-i", image_path, "-o", out]
            if self.model:
                cmd += ["-m", self.model]
            cmd += [ROLE + _cast_block(self.cast) + "\n\n" + PAGE_ASK]
            subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            raw = open(out).read().strip()
        finally:
            os.path.exists(out) and os.unlink(out)
        scene, self.cast = _split(raw, self.cast)
        return scene
