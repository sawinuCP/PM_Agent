"""Evidence coverage — scope gates before worker finish and synthesis gaps."""

from app.orchestration.coverage.entity_coverage import (
    entities_with_blocked_evidence,
    entities_with_health_evidence,
    entities_with_sprint_metrics_evidence,
    entity_finish_blocked_reason,
    synthesis_entity_gaps,
)
from app.orchestration.coverage.entity_scope import (
    issue_keys_in_text,
    project_keys_in_text,
    step_scope_entities,
)
from app.orchestration.coverage.gates import (
    coverage_gaps,
    finish_blocked_reason,
    task_requires_multi_entity_scope,
)
from app.orchestration.coverage.task_patterns import (
    is_blocked_task,
    is_health_task,
    is_sprint_metrics_task,
    task_text,
)

__all__ = [
    "coverage_gaps",
    "entities_with_blocked_evidence",
    "entities_with_health_evidence",
    "entities_with_sprint_metrics_evidence",
    "entity_finish_blocked_reason",
    "finish_blocked_reason",
    "is_blocked_task",
    "is_health_task",
    "is_sprint_metrics_task",
    "issue_keys_in_text",
    "project_keys_in_text",
    "step_scope_entities",
    "synthesis_entity_gaps",
    "task_requires_multi_entity_scope",
    "task_text",
]
