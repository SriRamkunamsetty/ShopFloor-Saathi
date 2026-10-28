"""Input screening for text and images. Defence in depth, not a substitute for IAM and approvals."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

MAX_TEXT_CHARS = 2000

_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
        r"disregard (all |any |the )?(previous|prior|above|system) (instructions|prompts|rules)",
        r"(reveal|show|print|repeat) (your|the) (system|hidden|initial) (prompt|instructions)",
        r"you are now (in )?(developer|dev|admin|god|dan) mode",
        r"act as (an? )?(unrestricted|jailbroken|unfiltered)",
        r"approve (the |this )?work ?order (yourself|automatically|now)",
        r"bypass (the )?(approval|lockout|loto|safety|interlock)",
    )
]

LOTO_NOTE = (
    "Apply lockout/tagout (LOTO) and verify zero energy before touching any machine part. "
    "This tool only advises; a trained technician must perform the work."
)

_IMAGE_MAGIC = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}


class GuardrailError(ValueError):
    """Raised when input fails validation and must be rejected."""


def sanitize_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Remove control characters, collapse whitespace and enforce a length limit."""
    if not isinstance(text, str):
        raise GuardrailError("Text input must be a string")
    cleaned = "".join(
        ch
        for ch in unicodedata.normalize("NFKC", text)
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        raise GuardrailError(f"Text is too long (max {max_chars} characters)")
    return cleaned


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def decode_image(image_base64: str, mime: str, max_bytes: int) -> bytes:
    """Decode and validate an uploaded image. Rejects wrong types, bad base64, oversize files."""
    if mime not in _IMAGE_MAGIC:
        raise GuardrailError("Unsupported image type")
    # Bound the work before decoding: base64 inflates size by ~4/3.
    if len(image_base64) > (max_bytes * 4) // 3 + 8:
        raise GuardrailError("Image is too large")
    try:
        data = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GuardrailError("Image is not valid base64") from exc
    if not data or len(data) > max_bytes:
        raise GuardrailError("Image is empty or too large")
    if not data.startswith(_IMAGE_MAGIC[mime]):
        raise GuardrailError("Image content does not match its declared type")
    return data
