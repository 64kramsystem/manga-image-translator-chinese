"""Translator backend that translates via the local `claude` CLI (Claude Code).

Selected with `--translator claude_cli`. Uses the `claude` binary in print mode
(`claude -p`), so it draws on a local Claude Code subscription rather than a paid
API key. MIT calls `_translate` once per page with that page's text regions, which
gives Claude the whole page as context for a coherent, in-character translation.

The model can be overridden with the SCANLATE_CLAUDE_MODEL env var (otherwise the
CLI default is used). Tuned for Hong Kong manhua (Cantonese-flavoured Chinese).
"""
import os
import re
import shutil
import subprocess
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
        # Optional per-page scene description: a no-arg callable the driver sets
        # before each page. Called only here, so pages without text cost nothing.
        self.scene_provider = None
        self.last_description = None
        # Running cast notes: recurring-character facts kept consistent across pages.
        # The driver seeds this; the describe pass then refreshes it each page (its
        # provider returns (scene_description, updated_cast)). Prepended to every
        # page's prompt so pronouns/identities stay consistent volume-wide.
        self.cast_notes = None

    async def _translate(self, from_lang: str, to_lang: str, queries: List[str]) -> List[str]:
        # to_lang arrives as the readable name (e.g. "English") via _LANGUAGE_CODE_MAP.
        if not queries:
            return []
        context = ""
        self.last_description = None
        if self.scene_provider is not None:
            provider, self.scene_provider = self.scene_provider, None
            try:
                self.last_description, new_cast = provider()
            except Exception as e:
                self.logger.warning(f"scene description failed: {e}")
                new_cast = None
            if new_cast:
                self.cast_notes = new_cast
            if self.last_description:
                context = ("Scene context for this page (for disambiguation — speaker gender, "
                           "who is addressed, tone):\n" + self.last_description.strip() + "\n\n")
        notes_section = ""
        if self.cast_notes:
            notes_section = ("Known facts about recurring characters (use for correct pronouns "
                             "and tone):\n" + self.cast_notes.strip() + "\n\n")
        prompt = PROMPT.format(tgt=to_lang) + notes_section + context + "\n".join(
            f"{i + 1}. {q}" for i, q in enumerate(queries)
        )
        out = self._call_claude(prompt)
        result = self._parse_numbered(out, len(queries))
        if result is None:
            out = self._call_claude(
                prompt + f"\n\nReturn EXACTLY {len(queries)} numbered lines, nothing else."
            )
            result = self._parse_numbered(out, len(queries))
        if result is None:  # last resort: take non-empty lines positionally
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            result = (lines + [""] * len(queries))[: len(queries)]
        return result

    def _call_claude(self, prompt: str) -> str:
        cmd = [CLAUDE_BIN]
        if self.model:
            cmd += ["--model", self.model]
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
