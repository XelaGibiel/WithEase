"""Self-learning correction memory ("Fehler-Gedächtnis").

When the user corrects a mis-recognised word, we remember "Whisper heard X, the
user meant Y" and apply it automatically to future transcripts.  This *feels*
like training but works instantly and entirely locally – no model fine-tuning.

Safety model (deliberately conservative):
  * Only single words are learned (phrases are skipped for v1).
  * A substitution becomes active only after the *same* correction is seen
    ``threshold`` times (default 2), so a one-off edit can't poison a common
    word after a single correction.
  * The learned list is inspectable and resettable from the settings page.

Persistence is via :meth:`to_dict` / :meth:`from_dict`; the module stores it in
its settings, which the core writes to disk.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


# A substitution corrected this many times (or more) is applied unconditionally;
# a fresher one only where Whisper was uncertain (see ErrorMemory.apply).
_STRONG = 2


def _fold(text: str) -> str:
    """Lower-case + strip diacritics (ä→a, ß→ss) for tolerant comparison."""
    text = text.lower().replace("ß", "ss")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def suggest_alternatives(wrong: str, pool: list[str],
                         limit: int = 5) -> list[str]:
    """Rank ``pool`` words by similarity to ``wrong`` (folded, edit-distance),
    closest first; drop the exact wrong word and near-misses that are too far;
    de-duplicate case-insensitively."""
    wrong = (wrong or "").strip()
    fw = _fold(wrong)
    if not fw:
        return []
    tol = max(2, len(fw) // 2)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for cand in pool:
        cand = (cand or "").strip()
        if not cand or cand == wrong:
            continue
        fc = _fold(cand)
        if not fc or fc in seen:
            continue
        if fc == fw:                     # e.g. a capitalisation fix
            dist = 0
        elif fc.startswith(fw) or fw.startswith(fc):
            dist = 1
        else:
            dist = _levenshtein(fc, fw)
            if dist > tol:
                continue
        seen.add(fc)
        scored.append((dist, cand))
    scored.sort(key=lambda x: (x[0], len(x[1])))
    return [c for _d, c in scored[:limit]]


def _match_case(src: str, value: str) -> str:
    """Give ``value`` the capitalisation pattern of the word it replaces."""
    if src.isupper() and len(src) > 1:
        return value.upper()
    if src[:1].isupper():
        return value[:1].upper() + value[1:]
    return value


class ErrorMemory:
    """Learns misheard→correct word substitutions and applies them."""

    def __init__(self, data: dict | None = None, threshold: int = 1) -> None:
        # threshold = how many identical corrections activate a substitution.
        # 1 = learn immediately (we only learn from explicit correction commands
        # anyway, so a single correction is already a deliberate signal).
        self._threshold = max(1, int(threshold))
        self._active: dict[str, str] = {}          # folded_misheard -> correct
        self._candidates: dict[str, dict] = {}     # folded_misheard -> {to,count}
        if data:
            self.from_dict(data)

    # -- learning -------------------------------------------------------

    def learn(self, old: str, new: str) -> bool:
        """Record a correction (``old`` was in the text, ``new`` was spoken).

        Returns ``True`` when this makes a *new* substitution active (so the
        caller knows it is worth persisting / announcing)."""
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new:
            return False
        if " " in old or " " in new:        # single words only (v1)
            return False
        key = _fold(old)
        if len(key) < 3 or key == _fold(new):
            return False
        # Self-correction: if a learned substitution currently PRODUCES ``old``
        # and the user is now changing ``old`` to something else, that
        # substitution over-corrected → forget it (never make things worse).
        for k in list(self._active):
            if _fold(self._active[k]) == key:
                self.remove(k)
        cand = self._candidates.get(key)
        if cand and _fold(cand["to"]) == _fold(new):
            cand["count"] += 1
        else:
            cand = {"to": new, "count": 1}
            self._candidates[key] = cand
        if cand["count"] >= self._threshold:
            became_active = self._active.get(key) != new
            self._active[key] = new
            return became_active
        return False

    # -- applying -------------------------------------------------------

    def apply(self, text: str, uncertain: list | set | None = None) -> str:
        """Replace whole-word occurrences of learned mis-hearings.

        To avoid over-correcting a word that was clearly spoken, a *fresh*
        substitution (seen once) is only applied where Whisper was **uncertain**
        (``uncertain`` = the low-confidence words of this utterance).  Once the
        same correction has been made ``_STRONG`` times it applies always."""
        if not self._active or not text:
            return text
        unc = {_fold(w) for w in uncertain} if uncertain else set()

        def repl(m: re.Match) -> str:
            word = m.group(0)
            fw = _fold(word)
            value = self._active.get(fw)
            if value is None:
                return word
            count = self._candidates.get(fw, {}).get("count", 1)
            if count >= _STRONG or fw in unc:
                return _match_case(word, value)
            return word

        return re.sub(r"\w+", repl, text, flags=re.UNICODE)

    def apply_all(self, text: str) -> str:
        """Apply *every* active substitution unconditionally – used for the live
        preview, where showing the user's own learned words matters more than
        caution (the final text is still re-checked by the Whisper polish)."""
        if not self._active or not text:
            return text

        def repl(m: re.Match) -> str:
            word = m.group(0)
            value = self._active.get(_fold(word))
            return _match_case(word, value) if value is not None else word

        return re.sub(r"\w+", repl, text, flags=re.UNICODE)

    def direct(self, word: str) -> str:
        """The learned correction for a single word (ungated), for suggestions."""
        return self._active.get(_fold(word), "")

    # -- inspection / persistence --------------------------------------

    def substitutions(self) -> dict[str, str]:
        """Active substitutions as ``{misheard: correct}`` (folded keys)."""
        return dict(self._active)

    def is_empty(self) -> bool:
        return not self._active and not self._candidates

    def remove(self, key: str) -> None:
        """Forget a single learned substitution (folded key)."""
        self._active.pop(key, None)
        self._candidates.pop(key, None)

    def set_target(self, key: str, value: str) -> None:
        """Edit the correct value of a learned substitution (folded key)."""
        value = (value or "").strip()
        if not key or not value:
            return
        self._active[key] = value
        if key in self._candidates:
            self._candidates[key]["to"] = value

    def clear(self) -> None:
        self._active.clear()
        self._candidates.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self._threshold,
            "active": dict(self._active),
            "candidates": {k: dict(v) for k, v in self._candidates.items()},
        }

    def from_dict(self, data: dict) -> None:
        # Keep the constructor's threshold (do not resurrect an old stored one),
        # so changing the default takes effect for existing users too.
        self._active = dict(data.get("active", {}))
        self._candidates = {k: dict(v)
                            for k, v in data.get("candidates", {}).items()}
        # Promote any candidate that already meets the (possibly lowered)
        # threshold – so earlier corrections show up after the default changed.
        for key, cand in self._candidates.items():
            if cand.get("count", 0) >= self._threshold:
                self._active.setdefault(key, cand["to"])
