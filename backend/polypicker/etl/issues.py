from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Issue:
    entity: str
    entity_key: str | None
    rule: str
    severity: str  # error | warning | info
    action: str    # dropped | fixed | flagged
    detail: dict[str, Any] | None = None


@dataclass
class IssueLog:
    """Collects data-quality issues during a run; persisted to data_quality_issues (FR-10.2)."""
    issues: list[Issue] = field(default_factory=list)

    def add(self, entity: str, key: Any, rule: str, severity: str, action: str, **detail: Any) -> None:
        self.issues.append(Issue(entity, None if key is None else str(key), rule, severity, action,
                                 detail or None))

    def summary(self) -> dict[str, int]:
        return dict(Counter(f"{i.entity}.{i.rule}.{i.action}" for i in self.issues))
