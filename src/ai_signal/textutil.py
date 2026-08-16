"""Shared text-cleaning helpers (stdlib only, zero dependencies)."""

import html
import re

_TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\n\f\v]+")
# A '<' with no closing '>' (truncation mid-tag): drop it and everything
# after - a malformed tag would swallow the rest in a browser anyway.
_DANGLING_RE = re.compile(r"<[^<]*$", re.S)
# Markdown links/images: [text](url) -> text; ![alt](url) -> (dropped).
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def strip_html_tags(text: str) -> str:
    """Remove HTML tags (script/style blocks first) and unescape entities.

    Also drops a ``<`` left behind by truncation mid-tag, so half-tags
    never reach rendered output.
    """
    cleaned = _TAG_RE.sub(" ", html.unescape(text or ""))
    cleaned = _DANGLING_RE.sub(" ", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def strip_markdown_links(text: str) -> str:
    """Turn markdown links/images into plain text (images dropped)."""
    cleaned = _MD_IMAGE_RE.sub(" ", text or "")
    cleaned = _MD_LINK_RE.sub(r"\1", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()
