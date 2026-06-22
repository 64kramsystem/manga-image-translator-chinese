"""Translator backend that translates via a local Ollama model — by default the
abliterated ("Heretic") Qwen3.6-27B build. Selected with `--translator heretic`.

Runs fully locally: no API key, no cloud, no rate limit. The whole volume is
translated in ONE client-side conversation: the cast note seeds a system message
and each page's source text + scene context is appended, so later pages carry the
context of earlier ones — names rendered consistently, character voices, earlier
phrasings. The driver resets the conversation per volume via start_volume().

Abliteration removes the model's refusals at near-zero capability loss, so adult,
violent or profane dialogue is translated faithfully rather than silently softened.

Override the model with SCANLATE_HERETIC_MODEL and the context window with
SCANLATE_HERETIC_NUM_CTX. Tuned for Hong Kong manhua (Cantonese-flavoured Chinese).
"""
import json
import os
import re
import urllib.request
from typing import List

from .common import CommonTranslator, VALID_LANGUAGES

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
HERETIC_MODEL = (os.environ.get("SCANLATE_HERETIC_MODEL")
                 or "hf.co/Youssofal/Qwen3.6-27B-Abliterated-Heretic-Uncensored-GGUF:Q6_K")
NUM_CTX = int(os.environ.get("SCANLATE_HERETIC_NUM_CTX", "32768"))
# Recent pages kept in the conversation (source + translation only, no scene context).
KEEP_PAGES = int(os.environ.get("SCANLATE_HERETIC_KEEP_PAGES", "40"))

SYSTEM = (
    "You are translating a Hong Kong manhua (Cantonese-flavoured Traditional Chinese comic) into "
    "natural, idiomatic {tgt}. Translate faithfully and completely — keep profanity, slang, sexual "
    "or violent content exactly as intended; never censor, soften or summarise. Each page you get "
    "is the text pieces (speech bubbles, captions, SFX) in reading order, OCR'd so they may contain "
    "minor errors. Translate each numbered item into punchy, natural comic {tgt} (not word-for-"
    "word); render onomatopoeia as {tgt}-style SFX; keep each line roughly as short as the original "
    "so it fits the bubble.\n"
    "Output ONLY a numbered list with the SAME numbering and item count as the input — one "
    "translation per line, no commentary, no original text, no blank lines."
)


class HereticTranslator(CommonTranslator):
    # key -> human-readable target name; permissive so any VALID_LANGUAGES target works.
    _LANGUAGE_CODE_MAP = VALID_LANGUAGES

    def __init__(self):
        super().__init__()
        self.model = HERETIC_MODEL
        self.timeout = int(os.environ.get("SCANLATE_HERETIC_TIMEOUT", "600"))
        # Optional per-page scene description: a no-arg callable the driver sets before
        # each page, returning the page's scene text. Called only here, so textless
        # pages cost nothing.
        self.scene_provider = None
        self.last_description = None
        # Cast note seeded by the driver per volume; pinned in the system message.
        self.cast_notes = None
        # Client-side rolling conversation (source + translation turns), reset per volume.
        self.messages = []

    def start_volume(self, cast_notes):
        """Reset the per-volume conversation and seed its cast note."""
        self.cast_notes = (cast_notes or "").strip() or None
        self.messages = []

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
        ask = (context + items + f"\n\nReturn ONLY the numbered list of exactly {len(queries)} "
               f"{to_lang} translations — same numbering, nothing else.")
        system = {"role": "system", "content": SYSTEM.format(tgt=to_lang) + self._cast_block()}

        out = self._ollama([system] + self.messages + [{"role": "user", "content": ask}])
        result = self._parse_numbered(out, len(queries))
        if result is None:  # one stricter retry within the same conversation
            retry = (f"That was not a valid list. Reply with EXACTLY {len(queries)} numbered "
                     f"{to_lang} translation lines for that page, nothing else.")
            out = self._ollama([system] + self.messages
                               + [{"role": "user", "content": ask},
                                  {"role": "assistant", "content": out},
                                  {"role": "user", "content": retry}])
            result = self._parse_numbered(out, len(queries))
        if result is None:  # last resort: take non-empty lines positionally
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            result = (lines + [""] * len(queries))[: len(queries)]

        # Remember this page (clean source + final translation) so later pages stay consistent;
        # scene context and boilerplate are dropped to bound the window.
        self.messages += [{"role": "user", "content": items},
                          {"role": "assistant",
                           "content": "\n".join(f"{i + 1}. {t}" for i, t in enumerate(result))}]
        if len(self.messages) > 2 * KEEP_PAGES:
            self.messages = self.messages[-2 * KEEP_PAGES:]
        return result

    def _cast_block(self):
        if not self.cast_notes:
            return ""
        return "\n\nRecurring characters (for correct pronouns/identity):\n" + self.cast_notes.strip()

    def _ollama(self, messages):
        req = {"model": self.model, "stream": False, "think": False,
               "options": {"num_ctx": NUM_CTX}, "messages": messages}
        r = urllib.request.urlopen(urllib.request.Request(
            OLLAMA + "/api/chat", data=json.dumps(req).encode(),
            headers={"Content-Type": "application/json"}), timeout=self.timeout)
        return json.load(r)["message"]["content"].strip()

    @staticmethod
    def _parse_numbered(text: str, n: int):
        """Parse '1. trans' lines into an n-length list, or None if too few parsed."""
        items = {}
        for m in re.finditer(r"(?m)^\s*(\d+)[.):\]]\s*(.*\S)?\s*$", text):
            items[int(m.group(1))] = (m.group(2) or "").strip()
        if len([i for i in range(1, n + 1) if i in items]) < n:
            return None
        return [items.get(i + 1, "") for i in range(n)]
