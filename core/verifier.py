"""Project-level verifier — re-exports ProjectVerifier for workflows.

Canonical implementation lives in ``editing.verifier.ProjectVerifier``.
"""

from __future__ import annotations

from editing.verifier import ProjectVerifier as Verifier

__all__ = ["Verifier"]
