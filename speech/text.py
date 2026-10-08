"""Validate plain spoken text without summarizing or truncating an answer."""

import re


def clean_text(text: str) -> str:
    # CosyVoice control tokens are not part of the answer's spoken content.
    text = re.sub(r"<\|[^<>]*\|>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return re.sub(r"\s+", " ", text).strip()
