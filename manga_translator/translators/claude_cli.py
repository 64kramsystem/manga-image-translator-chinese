"""Translator backend that translates via the local `claude` CLI (Claude Code).

Selected with `--translator claude_cli`. Uses the `claude` binary in print mode, so
it draws on a local Claude Code subscription rather than a paid API key. The whole
volume is translated in ONE persistent conversation: the first page opens a session
(`--session-id`), every later page resumes it (`--resume`), so each translation has
the full context of every prior page — names rendered consistently, character voices,
earlier phrasings. The driver resets the session per volume.

The model can be overridden with the SCANLATE_CLAUDE_MODEL env var (otherwise the
CLI default is used). Tuned for Hong Kong manhua (Cantonese-flavoured Chinese).
"""
import os
import re
import shutil
import subprocess
import uuid
from typing import List

from .common import CommonTranslator, VALID_LANGUAGES

CLAUDE_BIN = shutil.which("claude") or "claude"

PROMPT = (
    "You are translating a Hong Kong manhua (Cantonese-flavoured Traditional Chinese comic) "
    "into natural, idiomatic {tgt}. Below are the text pieces from ONE comic page — speech "
    "bubbles, captions and sound effects — in reading order, OCR'd so they may contain minor "
    "errors. Translate each numbered item into punchy, natural comic {tgt} (not word-for-word); "
    "render onomatopoeia as {tgt}-style SFX. Keep each translation roughly as short as the "
    "original so it fits the bubble.\n"
    "Output ONLY a numbered list with the SAME numbering and item count as the input — one "
    "translation per line, no commentary, no original text, no blank lines.\n\n"
)


class ClaudeCliTranslator(CommonTranslator):
    # key -> human-readable target name; permissive so any VALID_LANGUAGES target works.
    _LANGUAGE_CODE_MAP = VALID_LANGUAGES

    def __init__(self):
        super().__init__()
        self.model = os.environ.get("SCANLATE_CLAUDE_MODEL") or None
        self.timeout = int(os.environ.get("SCANLATE_CLAUDE_TIMEOUT", "240"))
        # Optional per-page scene description: a no-arg callable the driver sets before
        # each page, returning the page's scene text. Called only here, so textless
        # pages cost nothing.
        self.scene_provider = None
        self.last_description = None
        # Cast note seeded by the driver per volume (from the prior volume / --notes);
        # primes the conversation's opening turn.
        self.cast_notes = None
        # One claude conversation per volume (see module docstring). The driver resets
        # this to None at each volume start; the first page mints a session id.
        self.session_id = None

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        # to_lang arrives as the readable name (e.g. "English") via _LANGUAGE_CODE_MAP.
        if not queries:
            return []
        context = ""
        self.last_description = None
        if self.scene_provider is not None:
            provider, self.scene_provider = self.scene_provider, None
            try:
                self.last_description = provider()
            except Exception as e:
                self.logger.warning(f"scene description failed: {e}")
            if self.last_description:
                context = ("Scene context for this page (for disambiguation — speaker gender, "
                           "who is addressed, tone):\n" + self.last_description.strip() + "\n\n")
        items = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(queries))

        if self.session_id is None:  # first page of the volume: open the conversation
            self.session_id = str(uuid.uuid4())
            seed = ""
            if self.cast_notes:
                seed = ("Recurring characters established so far (for correct pronouns/identity):\n"
                        + self.cast_notes.strip() + "\n\n")
            prompt = PROMPT.format(tgt=to_lang) + seed + context + items
            out = self._call_claude(prompt, new=True)
        else:  # continue the same conversation — it already knows the task and the story
            prompt = ("Next page of the same comic.\n\n" + context + items
                      + f"\n\nReturn ONLY the numbered list of exactly {len(queries)} {to_lang} "
                      "translations — same numbering, nothing else.")
            out = self._call_claude(prompt, new=False)

        result = self._parse_numbered(out, len(queries))
        if result is None:  # one stricter retry within the same conversation
            out = self._call_claude(
                f"That was not a valid list. Reply with EXACTLY {len(queries)} numbered "
                f"{to_lang} translation lines for that page, nothing else.", new=False)
            result = self._parse_numbered(out, len(queries))
        if result is None:  # last resort: take non-empty lines positionally
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            result = (lines + [""] * len(queries))[: len(queries)]
        return result

    def _call_claude(self, prompt: str, new: bool) -> str:
        # --setting-sources "" loads no CLAUDE.md/settings, so the user's global memory
        # never enters the translation; OAuth subscription auth is unaffected.
        cmd = [CLAUDE_BIN, "--setting-sources", ""]
        if self.model:
            cmd += ["--model", self.model]
        cmd += ["--session-id", self.session_id] if new else ["--resume", self.session_id]
        cmd += ["-p", prompt]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
        return proc.stdout.strip()

    @staticmethod
    def _parse_numbered(text: str, n: int):
        """Parse '1. trans' lines into an n-length list, or None if too few parsed."""
        items = {}
        for m in re.finditer(r"(?m)^\s*(\d+)[.):\]]\s*(.*\S)?\s*$", text):
            items[int(m.group(1))] = (m.group(2) or "").strip()
        if len([i for i in range(1, n + 1) if i in items]) < n:
            return None
        return [items.get(i + 1, "") for i in range(n)]
