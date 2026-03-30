"""Sample project DAG for demo purposes."""

from chloe.graph.models import ProjectDAG, Task, TaskStatus, TaskType


def build_sample_dag() -> ProjectDAG:
    """A realistic software project with bug fixes blocking features blocking release."""
    tasks = [
        # Level 0 — no dependencies
        Task("T1", "Fix auth token refresh", TaskType.BUG_FIX, 2.0, TaskStatus.DONE),
        Task("T2", "Refactor DB pool", TaskType.REFACTOR, 4.0, TaskStatus.DONE),
        # Level 1
        Task("T3", "Rate limiting middleware", TaskType.FEATURE, 3.0, TaskStatus.DONE),
        Task("T4", "Write migration scripts", TaskType.DOCS, 2.0, TaskStatus.DONE),
        # Level 2
        Task("T5", "User dashboard API", TaskType.FEATURE, 6.0, TaskStatus.PENDING),
        Task("T6", "Add integration tests", TaskType.TEST, 3.0, TaskStatus.PENDING),
        # Level 3
        Task("T7", "Dashboard UI components", TaskType.FEATURE, 8.0, TaskStatus.PENDING),
        Task("T8", "Performance benchmarks", TaskType.TEST, 2.0, TaskStatus.PENDING),
        # Level 4
        Task("T9", "E2E test suite", TaskType.TEST, 4.0, TaskStatus.PENDING),
        Task("T10", "Update API docs", TaskType.DOCS, 2.0, TaskStatus.PENDING),
        # Level 5
        Task("T11", "Security audit", TaskType.REFACTOR, 3.0, TaskStatus.PENDING),
        # Level 6
        Task("T12", "v2.0 Release", TaskType.RELEASE, 1.0, TaskStatus.PENDING),
    ]

    dependencies = [
        # Level 0 → 1
        ("T1", "T3"),
        ("T2", "T4"),
        # Level 1 → 2
        ("T3", "T5"),
        ("T2", "T5"),
        ("T3", "T6"),
        ("T4", "T6"),
        # Level 2 → 3
        ("T5", "T7"),
        ("T5", "T8"),
        ("T6", "T8"),
        # Level 3 → 4
        ("T7", "T9"),
        ("T8", "T9"),
        ("T7", "T10"),
        # Level 4 → 5
        ("T9", "T11"),
        # Level 5 → 6
        ("T10", "T12"),
        ("T11", "T12"),
    ]

    return ProjectDAG(name="Project Phoenix v2.0", tasks=tasks, dependencies=dependencies)
