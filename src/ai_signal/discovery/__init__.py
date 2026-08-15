"""Phase 2C2 GitHub Discovery Policy Adapter (GitHub-specific).

This package owns the GitHub-specific discovery policy layer only:

* :mod:`ai_signal.discovery.policy` - immutable, versioned, in-code policy
  catalog (no YAML/DSL) with per-probe scope keys and spec hashes;
* :mod:`ai_signal.discovery.run` - the orchestration that runs a policy over
  the existing collection and candidate-qualification pipeline, enforces the
  scope/spec firewall before any network request, deduplicates candidates
  per run and applies the research budget.

It does NOT produce global Signals or Events, does not read README/Release
content, and does not emit Markdown reports.
"""

from .policy import (
    GitHubDiscoveryPolicy,
    GitHubDiscoveryProbe,
    POLICY_CATALOG,
    get_policy,
    list_policies,
)
from .run import GitHubDiscoveryRunResult, run_github_discovery

__all__ = [
    "GitHubDiscoveryPolicy",
    "GitHubDiscoveryProbe",
    "GitHubDiscoveryRunResult",
    "POLICY_CATALOG",
    "get_policy",
    "list_policies",
    "run_github_discovery",
]
