"""Per-volume scene description in ONE running conversation.

A Describer narrates a volume's pages to give the translator context — one page per
turn, in a single conversation — so a character's identity/gender stays consistent
across pages instead of being re-guessed each page. It is seeded with the volume's
cast note, and at the end emits the completed cast roster (which seeds the next
volume). Three backends:

  qwen   — local Ollama vision model (default qwen3.6:35b-a3b); history kept client-side
  claude — `claude` CLI, server-side session via --session-id/--resume
  codex  — `codex` CLI, stateless per page (no cross-page memory)

It only describes — never translates.
"""
import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

from PIL import Image

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
TIMEOUT = int(os.environ.get("SCANLATE_DESC_TIMEOUT", "300"))
CLAUDE_BIN = shutil.which("claude") or "claude"

ROLE = (
    "You are describing a Hong Kong manhua page by page to give a translator context. Use the "
    "established cast below to name characters and keep each one's gender/identity consistent — "
    "trust the cast over a single page's ambiguous art. For each page write ONE short paragraph: "
    "who is present (by cast name + gender), who speaks to whom in each bubble, the emotional "
    "tone, and any key on-panel action or object the dialogue refers to. Do NOT translate."
)
PAGE_ASK = "Describe this page."
CAST_ASK = (
    "Now list the full cast you established across this volume, one per line as "
    "`Name — gender — one distinguishing detail`. Include everyone recurring; drop one-off "
    "background figures. Output only the list."
)


def _cast_block(seed):
    return "\n\nEstablished cast:\n" + (seed if seed else "(none yet — build it as you go)")


def _b64(image_path):
    im = Image.open(image_path).convert("RGB")
    s = 1024 / max(im.size)
    if s < 1:
        im = im.resize((int(im.size[0] * s), int(im.size[1] * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


class Describer:
    """One scene-description conversation for a whole volume."""

    def __init__(self, backend, model=None, cast_seed=""):
        self.backend = backend
        self.model = model
        self.cast_seed = (cast_seed or "").strip()
        self.history = []        # qwen: client-side message list
        self.session_id = None   # claude: server-side session id

    def describe(self, image_path):
        return getattr(self, "_d_" + self.backend)(image_path)

    def cast(self):
        """The completed cast roster after the volume (seeds the next volume)."""
        return getattr(self, "_c_" + self.backend)()

    # ---- qwen: Ollama, client-side history (prior pages kept as text, current page as image) ----
    def _ollama(self, messages):
        req = {"model": self.model or "qwen3.6:35b-a3b", "stream": False, "think": False,
               "messages": messages}
        r = urllib.request.urlopen(urllib.request.Request(
            OLLAMA + "/api/chat", data=json.dumps(req).encode(),
            headers={"Content-Type": "application/json"}), timeout=600)
        return json.load(r)["message"]["content"].strip()

    def _d_qwen(self, image_path):
        if not self.history:
            self.history = [{"role": "system", "content": ROLE + _cast_block(self.cast_seed)}]
        scene = self._ollama(self.history + [{"role": "user", "content": PAGE_ASK,
                                              "images": [_b64(image_path)]}])
        # Keep history text-only (drop the image) so the payload stays bounded volume-wide.
        self.history += [{"role": "user", "content": PAGE_ASK},
                         {"role": "assistant", "content": scene}]
        return scene

    def _c_qwen(self):
        if not self.history:
            return self.cast_seed
        return self._ollama(self.history + [{"role": "user", "content": CAST_ASK}])

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
            prompt = (ROLE + _cast_block(self.cast_seed)
                      + f"\n\n{PAGE_ASK}\nThe page image is the file at: {image_path}")
            return self._claude(["--session-id", self.session_id], prompt)
        return self._claude(["--resume", self.session_id],
                            f"Next page.\n{PAGE_ASK}\nThe image is the file at: {image_path}")

    def _c_claude(self):
        if self.session_id is None:
            return self.cast_seed
        return self._claude(["--resume", self.session_id], CAST_ASK)

    # ---- codex: stateless per page (no cross-page memory) ----
    def _d_codex(self, image_path):
        with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
            out = f.name
        try:
            cmd = ["codex", "exec", "-i", image_path, "-o", out]
            if self.model:
                cmd += ["-m", self.model]
            cmd += [ROLE + _cast_block(self.cast_seed) + "\n\n" + PAGE_ASK]
            subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            return open(out).read().strip()
        finally:
            os.path.exists(out) and os.unlink(out)

    def _c_codex(self):
        return self.cast_seed
