"""Intent audit — the deterministic structural half of intended-vs-implemented.

The ``intended-vs-implemented`` skill is fundamentally an LLM method: read the
documented intent, read the code that should enforce it, compare claim to code
one boundary at a time, and classify each mismatch by whether crossing it
matters.  That semantic comparison runs in the LLM review agent
(``delivery_review_node``).

This module is the *deterministic* precondition that makes that audit
possible: it verifies that the durable intent documents (the
``shipping-artifacts`` core set) actually exist for the run.  Without recorded
intent there is nothing to compare the code against, and the skill is explicit
that "you cannot audit intent you never recorded."

Design rules (kept deliberately simple to avoid noisy false positives):

* It never fabricates intent.  If the docs are silent, it says the docs are
  silent.
* When no documentation directory exists at all it reports ``NO_INTENT_DOCS``
  and produces no findings.  This keeps the deterministic test runtime green
  (the stub architect in tests never writes docs) while real LLM runs, having
  produced the docs, get a real audit.
* It scans the run-scoped ``documentation/`` directory first (so concurrent
  runs do not collide), then falls back to a repo-root ``documentation/``
  directory for manually authored docs.
* "Doc present but a core doc missing" is a deterministic, citable gap — the
  intent record is incomplete — and is the one ``GAP_FOUND`` the deterministic
  conformance layer can produce on its own.  Semantic code-vs-intent gaps stay
  the review agent's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runs import GraphRunStore, utc_now

# The shipping-artifacts core doc set — every reviewable change is expected to
# carry these.  ``architecture.md`` is the root document; the rest are the
# surfaces an intent audit compares the code against.
CORE_INTENT_DOCS = [
    "architecture.md",
    "permissions.md",
    "flows.md",
    "variables.md",
    "tests.md",
]

# Overall status values the intent audit can yield.
NO_INTENT_DOCS = "NO_INTENT_DOCS"
INTENT_INCOMPLETE = "INTENT_INCOMPLETE"
INTENT_PRESENT = "INTENT_PRESENT"


@dataclass(frozen=True)
class IntentAuditResult:
    """Structural intent-document audit for a single GraphRun."""

    status: str
    doc_source: str | None
    present_docs: list[str] = field(default_factory=list)
    missing_core_docs: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    audited_at: str = field(default_factory="")

    def to_report_section(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "doc_source": self.doc_source,
            "present_docs": list(self.present_docs),
            "missing_core_docs": list(self.missing_core_docs),
            "findings": list(self.findings),
            "audited_at": self.audited_at,
        }


def run_intent_audit(root: Path, run_id: str) -> IntentAuditResult:
    """Audit the presence of the shipping-artifacts core intent docs.

    Returns ``NO_INTENT_DOCS`` (no findings) when no documentation directory
    exists at all — the legacy / test-runtime case.  Returns
    ``INTENT_INCOMPLETE`` (one finding per missing core doc) when some docs
    exist but a core doc is absent.  Returns ``INTENT_PRESENT`` when the full
    core set is present.
    """
    store = GraphRunStore(root)
    run_doc_dir = store.run_dir(run_id) / "documentation"
    repo_doc_dir = root / "documentation"

    doc_dir, source = _resolve_doc_dir(run_doc_dir, repo_doc_dir)
    if doc_dir is None:
        return IntentAuditResult(
            status=NO_INTENT_DOCS,
            doc_source=None,
            present_docs=[],
            missing_core_docs=list(CORE_INTENT_DOCS),
            findings=[],
            audited_at=utc_now(),
        )

    present = sorted(p.name for p in doc_dir.glob("*.md"))
    present_set = set(present)
    missing = sorted(set(CORE_INTENT_DOCS) - present_set)
    findings = [
        {
            "type": "missing_core_intent_doc",
            "doc": name,
            "documented_intent": None,
            "implemented_reality": None,
            "boundary": "documentation",
            "attacker": "any reviewer or agent relying on recorded intent",
            "victim": "auditability of the change",
            "fix": f"architect must produce {name} under documentation/ "
            "(see the shipping-artifacts skill: core docs)",
        }
        for name in missing
    ]
    status = INTENT_INCOMPLETE if missing else INTENT_PRESENT
    return IntentAuditResult(
        status=status,
        doc_source=source,
        present_docs=present,
        missing_core_docs=missing,
        findings=findings,
        audited_at=utc_now(),
    )


def _resolve_doc_dir(run_doc_dir: Path, repo_doc_dir: Path) -> tuple[Path | None, str | None]:
    """Prefer run-scoped docs; fall back to repo-root docs.

    Returns the directory containing at least one ``*.md`` file and a label
    describing which source was chosen, or ``(None, None)`` when neither holds
    any markdown.
    """
    if run_doc_dir.is_dir() and any(run_doc_dir.glob("*.md")):
        return run_doc_dir, "run"
    if repo_doc_dir.is_dir() and any(repo_doc_dir.glob("*.md")):
        return repo_doc_dir, "repo"
    return None, None
