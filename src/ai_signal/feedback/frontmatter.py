"""Strict YAML-compatible frontmatter parsing and serialization.

The frontmatter is delimited by ``---`` lines and contains only JSON-scalar
values (strings, integers, ``null``). This restricted subset is intentionally
easy to parse without a full YAML dependency, while remaining valid YAML.

Strictness rules:

* duplicate keys are rejected (no last-value-wins);
* double-quoted strings are parsed with :func:`json.loads` (correct backslash /
  quote handling) and serialized with :func:`json.dumps`;
* control characters are rejected everywhere.

Example::

    ---
    schema_version: 1
    target_type: "material_pack"
    target_id: "<pack_id>"
    decision: null
    ---
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping, Optional, Tuple

_ALLOWED_FIELDS = (
    "schema_version",
    "target_type",
    "target_id",
    "event_id",
    "week_key",
    "decision",
    "reason",
    "audience",
    "angle",
    "usefulness",
    "published_url",
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


class FrontmatterError(ValueError):
    """Raised when frontmatter cannot be strictly parsed."""


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value == "" or value.lower() == "null" or value == "~":
        return None
    if value.startswith('"'):
        if not value.endswith('"') or len(value) < 2:
            raise FrontmatterError("unterminated double-quoted string")
        try:
            inner = json.loads(value)
        except ValueError as exc:
            raise FrontmatterError("invalid double-quoted string") from exc
        if not isinstance(inner, str):
            raise FrontmatterError("expected a string")
        if _CONTROL_CHARS.search(inner):
            raise FrontmatterError("control character in string")
        return inner
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise FrontmatterError("unterminated single-quoted string")
        inner = value[1:-1]
        if _CONTROL_CHARS.search(inner):
            raise FrontmatterError("control character in string")
        return inner
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError as exc:
            raise FrontmatterError("invalid integer") from exc
    if _CONTROL_CHARS.search(value):
        raise FrontmatterError("control character in scalar")
    return value


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split ``text`` into (frontmatter dict, body).

    Raises :class:`FrontmatterError` if the frontmatter block is missing,
    malformed, contains duplicate or unknown fields.
    """
    if not text.startswith("---"):
        raise FrontmatterError("missing opening delimiter")
    lines = text.split("\n")
    if lines[0].strip() != "---":
        raise FrontmatterError("missing opening delimiter")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise FrontmatterError("missing closing delimiter")
    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1:])
    result: Dict[str, Any] = {}
    for lineno, raw_line in enumerate(fm_lines, start=1):
        line = raw_line.rstrip()
        if line.strip() == "":
            continue
        m = _KV_RE.match(line)
        if not m:
            raise FrontmatterError("line %d: not key: value" % lineno)
        key, value = m.group(1), m.group(2)
        if key not in _ALLOWED_FIELDS:
            raise FrontmatterError("line %d: unknown field %r" % (lineno, key))
        if key in result:
            raise FrontmatterError("line %d: duplicate field %r" % (lineno, key))
        result[key] = _parse_scalar(value)
    return result, body


def serialize_frontmatter(data: Mapping[str, Any]) -> str:
    """Serialize a mapping to strict frontmatter text (``---`` delimited)."""
    lines = ["---"]
    for key in _ALLOWED_FIELDS:
        if key not in data:
            continue
        lines.append("%s: %s" % (key, _format_scalar(data[key])))
    lines.append("---")
    return "\n".join(lines)


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if _CONTROL_CHARS.search(value):
            raise FrontmatterError("control character in value")
        return json.dumps(value, ensure_ascii=False)
    raise FrontmatterError("unsupported scalar type: %s" % type(value).__name__)
