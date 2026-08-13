"""AI Signal Agent - shadow rewrite foundation.

Phase 1 delivers offline infrastructure only: immutable configuration,
domain models, a state machine, source contracts, a local SQLite store
with migrations, an offline CLI and structured redacted logging.

Nothing in this package performs real collection, calls an LLM, builds a
material pack, publishes to an Obsidian vault or switches email delivery.
The legacy pipeline (``python main.py`` and the weekly GitHub Action) is
intentionally untouched and remains the source of truth in production.
"""

__version__ = "0.1.0"
