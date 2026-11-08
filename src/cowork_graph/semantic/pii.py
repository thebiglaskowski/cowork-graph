"""PII detection at ingest, masking at serve time — ported from cowork-rag-chat.

Policy (its CLAUDE.md invariant 9, unchanged): identifiers get masked as
[pii …last4]; names, companies, usernames, URLs and dates of birth are
inventory-only, because a personal corpus is full of names and masking them
guts answers about people. Masking is whole-token and boundary-anchored with a
≥6-alphanumeric substance gate — detector fragments like "LS" or "50" once
corrupted amounts file-wide when they were literal-replaced.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Iterable, Sequence

# Pinned Hub revision: the model needs trust_remote_code, so an unpinned load
# would execute whatever the upstream repo ships tomorrow. Bump deliberately.
PII_MODEL = "LiquidAI/LFM2.5-Encoder-350M-PII-Detector"
PII_REVISION = "b8c9cf3d2d6ae52501b35a27ba46f271449c9ce2"
PII_THRESHOLD = 0.5
MASK_MIN_ALNUM = 6
MAX_VALUES_PER_FILE = 500


def _alnum(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", value)


def mask_worthy(value: str) -> bool:
    return len(_alnum(value)) >= MASK_MIN_ALNUM


def maskable(label: str, mask_labels: Sequence[str]) -> bool:
    if "all" in mask_labels:
        return True
    return any(str(label).startswith(p) for p in mask_labels)


def _replacement(value: str) -> str:
    alnum = _alnum(value)
    return f"[pii …{alnum[-4:]}]" if len(alnum) >= 6 else "[pii]"


def _pattern(values: list[str]) -> re.Pattern:
    parts = []
    for v in values:
        prefix = r"(?<![0-9A-Za-z])" if re.match(r"[0-9A-Za-z]", v) else ""
        suffix = r"(?![0-9A-Za-z])" if re.search(r"[0-9A-Za-z]$", v) else ""
        parts.append(prefix + re.escape(v) + suffix)
    return re.compile("|".join(parts))


def mask(text: str, values: Iterable[str]) -> str:
    """Replace whole-token occurrences, longest value first so a short value
    can't split a longer one that contains it."""
    vals = sorted({v for v in values if v and mask_worthy(v)}, key=len, reverse=True)
    if not vals or not text:
        return text
    return _pattern(vals).sub(lambda m: _replacement(m.group(0)), text)


def maskable_values(rows: Iterable[tuple[str, str]], mask_labels: Sequence[str]) -> list[str]:
    return [value for label, value in rows if maskable(label, mask_labels) and mask_worthy(value)]


# ---------------------------------------------------------------------------
# Memory guard fallback — regex, always on, independent of the model.
# ---------------------------------------------------------------------------

_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_DIGIT_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def obvious_identifiers(text: str) -> list[str]:
    """SSNs and card-like numbers (Luhn-valid 13–19 digits). Cheap and certain;
    the model catches the rest when installed."""
    found = [m.group(0) for m in _SSN.finditer(text)]
    for m in _DIGIT_RUN.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(m.group(0))
    return found


# ---------------------------------------------------------------------------
# Optional local detector (the `pii` dependency group)
# ---------------------------------------------------------------------------


class Detector:
    def __init__(self, mode: str, device: str):
        self.mode = mode  # auto | on | off
        self.device = device
        self._pipe = None
        self._lock = threading.Lock()
        self._warned = False

    def available(self) -> bool:
        if self.mode == "off":
            return False
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            if self.mode == "on":
                raise RuntimeError(
                    "pii_detect = on but the `pii` group is missing; "
                    "run: uv sync --group mcp --group semantic --group pii"
                ) from None
            if not self._warned:
                self._warned = True
                print("PII detection skipped (`pii` group not installed)", file=sys.stderr)
            return False
        return True

    def _load(self):
        if self._pipe is None:
            os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
            from transformers import pipeline

            self._pipe = pipeline(
                "token-classification",
                model=PII_MODEL,
                revision=PII_REVISION,
                trust_remote_code=True,
                aggregation_strategy="simple",
                device=self.device,
            )
        return self._pipe

    @staticmethod
    def _collect(text: str, entities) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for ent in entities:
            if float(ent.get("score", 0.0)) < PII_THRESHOLD:
                continue
            value = text[ent["start"] : ent["end"]].strip()
            if len(value) < 3 or not any(c.isalnum() for c in value):
                continue
            group = str(ent.get("entity_group", ent.get("entity", "pii"))).lower()
            vals = found.setdefault(group, [])
            if value not in vals:
                vals.append(value)
        return found

    def detect_many(self, texts: Sequence[str]) -> dict[str, list[str]]:
        """Merged {label: [values]} over a file's chunks."""
        todo = [t.strip() for t in texts if t and t.strip()]
        merged: dict[str, list[str]] = {}
        if not todo:
            return merged
        with self._lock:
            outputs = self._load()(todo, batch_size=16)
        total = 0
        for text, entities in zip(todo, outputs):
            for label, vals in self._collect(text, entities).items():
                bucket = merged.setdefault(label, [])
                for v in vals:
                    if v not in bucket and total < MAX_VALUES_PER_FILE:
                        bucket.append(v)
                        total += 1
        return merged
