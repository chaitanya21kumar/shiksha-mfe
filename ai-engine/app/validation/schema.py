"""What a validation pass reports.

Deliberately separate from the artefact contracts. A `DocumentInsights` says what
was generated; a `ValidationReport` says what we then found wrong with it, and the
two have different lifetimes — the report is advice to a caller, not part of the
learning content, and it must never end up inside a package.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

#: `error` means do not ship this without a human looking at it. `warning` means
#: a person should know, but the artefact is still usable. Nothing here blocks
#: automatically: spelling is a quality signal, not a correctness one, and a
#: pipeline that refused to return a lesson over one flagged word would be worse
#: than one that hands it over with a note attached.
Severity = Literal["warning", "error"]

Status = Literal["passed", "passed_with_warnings", "failed", "not_run"]


class ValidationIssue(BaseModel):
    """One thing found wrong, located precisely enough to act on."""

    code: str = Field(description='Stable machine-readable cause, e.g. "spelling.unknown_word".')
    severity: Severity = "warning"
    field_path: str = Field(
        description='Where it is, in dotted form, e.g. "glossary.2.definition" or "summary".'
    )
    message: str = Field(description="What a person needs to read.")
    actual: str | None = Field(
        default=None, description="The offending fragment, quoted exactly as generated."
    )
    suggestion: str | None = Field(
        default=None,
        description=(
            "A possible correction, for a human to accept or reject. The engine never "
            "applies one on its own."
        ),
    )
    validator_name: str = Field(description="Which check produced this.")


class ValidationReport(BaseModel):
    """The outcome of running the checks over one generated artefact."""

    status: Status = "passed"
    checks_run: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    #: Server-owned, always. A model may not tell us when something happened, for
    #: the same reason it may not tell us its own word count.
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    skipped: list[str] = Field(
        default_factory=list,
        description=(
            "Checks that could not run, each with the reason. A check that was skipped is "
            "not a check that passed, and the difference has to survive into the report."
        ),
    )

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def finalise(self) -> ValidationReport:
        """Set `status` from what was actually found."""
        if not self.checks_run:
            self.status = "not_run"
        elif self.errors:
            self.status = "failed"
        elif self.issues:
            self.status = "passed_with_warnings"
        else:
            self.status = "passed"
        return self

    def as_warnings(self) -> list[str]:
        """Flatten into the plain-string warnings the artefact contracts already carry.

        The pipelines have one place a caller already looks. Adding a second,
        differently-shaped channel for the same kind of information would mean a
        caller who checks only `warnings` silently misses these.
        """
        return [f"{issue.field_path}: {issue.message}" for issue in self.issues]
