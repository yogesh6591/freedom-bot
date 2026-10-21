"""Normalize inbound email bodies and wrap AI reply text for Instantly HTML."""

from __future__ import annotations

import re
import secrets

# Gmail / Apple Mail / Outlook-style quote headers.
_QUOTE_HEADER = re.compile(
    r"(?:^|\n)\s*(?:"
    r"On\s.+?wrote:\s*"
    r"|From:\s.+"
    r"|-----Original Message-----"
    r"|_{5,}"
    r"|Begin forwarded message:"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_QUOTED_LINE = re.compile(r"^\s*>+")


def strip_email_quotes(body: str) -> str:
    """Keep the newest reply text; drop nested quote history."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    match = _QUOTE_HEADER.search(text)
    if match and match.start() > 0:
        text = text[: match.start()].rstrip()
    elif match and match.start() == 0:
        text = text

    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        if _QUOTED_LINE.match(line):
            break
        if _QUOTE_HEADER.match(line) or _QUOTE_HEADER.match("\n" + line):
            break
        if re.match(r"^On\s+.+\bwrote:\s*$", line.strip(), re.IGNORECASE):
            break
        kept.append(line)

    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned or text.split("\n")[0].strip()


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def break_long_answer_paragraphs(body: str) -> str:
    """Layout-only: blank line between sentences in dense answer paragraphs.

    Does not change wording — only inserts paragraph breaks so Gmail does not
    show one long wrapping line. Leaves CTA / URL / Best, blocks alone.
    """
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return text

    paragraphs = text.split("\n\n")
    out: list[str] = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            continue
        first = stripped.split("\n", 1)[0].strip().lower()
        if first.startswith("you're welcome to take our free assessment"):
            out.append(stripped)
            continue
        if first.startswith("http://") or first.startswith("https://"):
            out.append(stripped)
            continue
        if re.match(r"^best\s*,?\s*$", first, re.IGNORECASE):
            out.append(stripped)
            continue
        # Multi-sentence single paragraph → one sentence per paragraph
        if "\n" not in stripped and _SENTENCE_SPLIT.search(stripped):
            parts = [p.strip() for p in _SENTENCE_SPLIT.split(stripped) if p.strip()]
            if len(parts) >= 2:
                out.extend(parts)
                continue
        out.append(stripped)
    return "\n\n".join(out)


def plain_text_to_email_html(body: str) -> str:
    """Wrap AI plain-text for Instantly/Gmail delivery only (no wording changes).

    Attaches a trailing ``Best,`` / name paragraph to the prior block with a
    single line break so Gmail is less likely to hide it behind "…", and adds a
    hidden unique marker so the message suffix is not identical across the thread.
    """
    lowered = (body or "").lower()
    if "<p" in lowered or "<div" in lowered:
        return body

    normalized = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return "<p></p>"

    paragraphs = [p for p in normalized.split("\n\n") if p.strip()]
    if not paragraphs:
        return f"<p>{_html_escape_keep_br(normalized)}</p>"

    # Keep Best,/name with the prior block (helps Gmail …) but leave a blank
    # line before Best so the close is visually spaced.
    if len(paragraphs) >= 2 and _looks_like_signoff_paragraph(paragraphs[-1]):
        signoff = paragraphs.pop().strip()
        paragraphs[-1] = f"{paragraphs[-1].rstrip()}\n\n{signoff}"

    parts: list[str] = []
    for paragraph in paragraphs:
        parts.append(f"<p>{_html_escape_keep_br(paragraph)}</p>")

    marker = secrets.token_hex(4)
    parts.append(
        f'<div style="mso-hide:all;display:none;max-height:0;overflow:hidden;'
        f'font-size:0;line-height:0;">fb:{marker}</div>'
    )
    return "".join(parts)


def _looks_like_signoff_paragraph(paragraph: str) -> bool:
    first = paragraph.split("\n", 1)[0].strip()
    return bool(re.match(r"^best\s*,?\s*$", first, re.IGNORECASE))


def _html_escape_keep_br(text: str) -> str:
    escaped = (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return escaped.replace("\n", "<br>")
