"""Deterministic Meridian Software project-management world.

The seed preserves the original Platform Delivery contract used by demos and
tests (PLAT-1..PLAT-11, SPR-1..SPR-6 velocities, board BRD-1) and extends it
with related projects, people, releases, links, comments, and history so an
AI project manager can reason about delivery — not just list tickets.

Simulation clock is pinned (`AS_OF`) so overdue/health calculations stay
reproducible regardless of the host date.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

AS_OF = date(2026, 9, 5)

USERS: dict[str, dict[str, Any]] = {
    "alice": {
        "account_id": "alice", "display_name": "Alice Chen",
        "role": "Senior Backend Engineer", "team": "Platform",
    },
    "bob": {
        "account_id": "bob", "display_name": "Bob Okonkwo",
        "role": "Backend Engineer", "team": "Platform",
    },
    "carol": {
        "account_id": "carol", "display_name": "Carol Nguyen",
        "role": "Full-stack Engineer", "team": "Platform",
    },
    "dave": {
        "account_id": "dave", "display_name": "Dave Patel",
        "role": "Backend Engineer", "team": "Platform",
    },
    "erin": {
        "account_id": "erin", "display_name": "Erin Walsh",
        "role": "QA Engineer", "team": "Platform",
    },
    "frank": {
        "account_id": "frank", "display_name": "Frank Rossi",
        "role": "Backend Engineer", "team": "Platform",
    },
    "sarah": {
        "account_id": "sarah", "display_name": "Sarah Kim",
        "role": "Product Manager", "team": "Platform",
    },
    "maya": {
        "account_id": "maya", "display_name": "Maya Brooks",
        "role": "QA Lead", "team": "Platform",
    },
    "leo": {
        "account_id": "leo", "display_name": "Leo Alvarez",
        "role": "Mobile Engineer", "team": "Nova",
    },
    "priya": {
        "account_id": "priya", "display_name": "Priya Shah",
        "role": "Payments Engineer", "team": "Payments",
    },
    "nina": {
        "account_id": "nina", "display_name": "Nina Volkov",
        "role": "Data Engineer", "team": "Analytics",
    },
    "tom": {
        "account_id": "tom", "display_name": "Tom Ike",
        "role": "Engineering Manager", "team": "Platform",
    },
    "unassigned": {
        "account_id": "unassigned", "display_name": "Unassigned",
        "role": "", "team": "",
    },
}

PROJECTS: dict[str, dict[str, Any]] = {
    "PLAT": {
        "key": "PLAT",
        "name": "Platform Delivery",
        "lead": "sarah",
        "category": "Platform",
        "description": (
            "Shared checkout, session, and webhook platform that other Meridian "
            "products depend on. Release 2.5.0 is the current delivery target."
        ),
        "members": ["alice", "bob", "carol", "dave", "erin", "frank", "sarah", "maya", "tom"],
        "board_id": "BRD-1",
    },
    "NOVA": {
        "key": "NOVA",
        "name": "Nova Mobile Platform",
        "lead": "leo",
        "category": "Mobile",
        "description": (
            "Consumer iOS and Android app. Offline checkout and biometric login "
            "are the 3.1.0 themes."
        ),
        "members": ["leo", "carol", "maya", "sarah", "tom"],
        "board_id": "BRD-2",
    },
    "ANLT": {
        "key": "ANLT",
        "name": "Customer Analytics",
        "lead": "nina",
        "category": "Data",
        "description": "Event pipeline, funnels, and retention reporting for product.",
        "members": ["nina", "sarah", "tom"],
        "board_id": "BRD-3",
    },
    "PAY": {
        "key": "PAY",
        "name": "Payment Platform",
        "lead": "priya",
        "category": "Payments",
        "description": "Ledger, settlements, and refunds that checkout depends on.",
        "members": ["priya", "dave", "sarah", "tom"],
        "board_id": "BRD-4",
    },
}

BOARDS: dict[str, dict[str, Any]] = {
    "BRD-1": {"name": "Platform Delivery", "project": "PLAT", "type": "scrum"},
    "BRD-2": {"name": "Nova Mobile", "project": "NOVA", "type": "scrum"},
    "BRD-3": {"name": "Analytics Delivery", "project": "ANLT", "type": "kanban"},
    "BRD-4": {"name": "Payments Delivery", "project": "PAY", "type": "scrum"},
}


def _sprint(*, sprint_id: str, name: str, state: str, board_id: str,
            committed: int, completed: int, start: date, end: date,
            goal: str = "") -> dict[str, Any]:
    return {
        "id": sprint_id,
        "name": name,
        "state": state,
        "board_id": board_id,
        "committed_points": committed,
        "completed_points": completed,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "goal": goal,
    }


def _comment(author: str, body: str, when: date) -> dict[str, Any]:
    return {"author": author, "body": body, "date": when.isoformat(),
            "created": when.isoformat()}


def _change(author: str, field: str, frm: str, to: str, when: date) -> dict[str, Any]:
    return {"at": when.isoformat(), "author": author, "field": field,
            "from": frm, "to": to}


def _issue(
    key: str, summary: str, issue_type: str, status: str, points: int,
    assignee: str, sprint: str, *,
    project: str | None = None,
    priority: str = "Medium",
    reporter: str = "sarah",
    labels: list[str] | None = None,
    components: list[str] | None = None,
    fix_versions: list[str] | None = None,
    epic: str | None = None,
    parent: str | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    created: str = "2026-08-01",
    updated: str | None = None,
    blocked_by: list[str] | None = None,
    blocks: list[str] | None = None,
    comments: list[dict[str, Any]] | None = None,
    changelog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prefix = project or key.split("-", 1)[0]
    return {
        "id": key,
        "key": key,
        "summary": summary,
        "type": issue_type,
        "status": status,
        "story_points": points,
        "assignee": assignee,
        "reporter": reporter,
        "priority": priority,
        "project": prefix,
        "sprint": sprint,
        "epic": epic,
        "parent": parent,
        "labels": labels or [],
        "components": components or [],
        "fix_versions": fix_versions or [],
        "due_date": due_date,
        "start_date": start_date,
        "created": created,
        "updated": updated or created,
        "blocked_by": blocked_by or [],
        "blocks": blocks or [],
        "comments": comments or [],
        "changelog": changelog or [],
    }


def build_world() -> dict[str, Any]:
    """Return a fresh, fully related project-management snapshot."""
    sprints: dict[str, dict[str, Any]] = {}
    velocities = [30, 42, 38, 45, 36]
    for idx, completed in enumerate(velocities, start=1):
        sprints[f"SPR-{idx}"] = _sprint(
            sprint_id=f"SPR-{idx}", name=f"Sprint {idx}", state="closed",
            board_id="BRD-1",
            committed=completed + (idx % 3) * 2, completed=completed,
            start=date(2026, 5, 4) + timedelta(weeks=2 * (idx - 1)),
            end=date(2026, 5, 18) + timedelta(weeks=2 * (idx - 1)),
            goal=f"Closed Platform sprint {idx}",
        )
    sprints["SPR-6"] = _sprint(
        sprint_id="SPR-6", name="Sprint 6", state="active",
        board_id="BRD-1", committed=24, completed=8,
        start=date(2026, 8, 31), end=date(2026, 9, 14),
        goal="Stabilize checkout retries and ship the sprint health dashboard",
    )
    sprints["NOVA-SPR-2"] = _sprint(
        sprint_id="NOVA-SPR-2", name="Nova Sprint 2", state="closed",
        board_id="BRD-2", committed=21, completed=18,
        start=date(2026, 8, 3), end=date(2026, 8, 16),
        goal="Ship biometric login",
    )
    sprints["NOVA-SPR-3"] = _sprint(
        sprint_id="NOVA-SPR-3", name="Nova Sprint 3", state="active",
        board_id="BRD-2", committed=20, completed=5,
        start=date(2026, 8, 31), end=date(2026, 9, 14),
        goal="Offline cart sync for release 3.1.0",
    )
    sprints["ANLT-SPR-1"] = _sprint(
        sprint_id="ANLT-SPR-1", name="Analytics Sprint 1", state="closed",
        board_id="BRD-3", committed=13, completed=13,
        start=date(2026, 8, 3), end=date(2026, 8, 16),
        goal="Cohort retention v1",
    )
    sprints["ANLT-SPR-2"] = _sprint(
        sprint_id="ANLT-SPR-2", name="Analytics Sprint 2", state="active",
        board_id="BRD-3", committed=13, completed=3,
        start=date(2026, 8, 31), end=date(2026, 9, 14),
        goal="Real-time funnel lag visibility",
    )
    sprints["PAY-SPR-1"] = _sprint(
        sprint_id="PAY-SPR-1", name="Pay Sprint 1", state="closed",
        board_id="BRD-4", committed=16, completed=13,
        start=date(2026, 8, 3), end=date(2026, 8, 16),
        goal="Ledger posting foundation",
    )
    sprints["PAY-SPR-2"] = _sprint(
        sprint_id="PAY-SPR-2", name="Pay Sprint 2", state="active",
        board_id="BRD-4", committed=18, completed=4,
        start=date(2026, 8, 31), end=date(2026, 9, 14),
        goal="Unblock refunds and fix settlement timezone drift",
    )

    versions = [
        {"id": "PLAT-V-2.4", "project": "PLAT", "name": "2.4.0",
         "released": True, "release_date": "2026-08-20",
         "description": "OAuth login and token refresh hardening"},
        {"id": "PLAT-V-2.5", "project": "PLAT", "name": "2.5.0",
         "released": False, "release_date": "2026-09-18",
         "description": "Checkout reliability and sprint health dashboard"},
        {"id": "NOVA-V-3.1", "project": "NOVA", "name": "3.1.0",
         "released": False, "release_date": "2026-09-25",
         "description": "Offline checkout and iOS 18 token fix"},
        {"id": "ANLT-V-1.2", "project": "ANLT", "name": "1.2.0",
         "released": True, "release_date": "2026-08-18",
         "description": "Cohort retention"},
        {"id": "ANLT-V-1.3", "project": "ANLT", "name": "1.3.0",
         "released": False, "release_date": "2026-09-30",
         "description": "Real-time funnel"},
        {"id": "PAY-V-1.8", "project": "PAY", "name": "1.8.0",
         "released": False, "release_date": "2026-09-22",
         "description": "Ledger v2 and settlement correctness"},
    ]

    issues: list[dict[str, Any]] = [
        _issue("PLAT-1", "Implement OAuth login", "Story", "Done", 5, "alice", "SPR-1",
               priority="High", labels=["auth"], components=["Authentication"],
               fix_versions=["2.4.0"], changelog=[
                   _change("alice", "status", "To Do", "In Progress", date(2026, 5, 5)),
                   _change("alice", "status", "In Progress", "Done", date(2026, 5, 12)),
               ]),
        _issue("PLAT-2", "Fix token refresh loop", "Bug", "Done", 3, "bob", "SPR-1",
               priority="High", labels=["auth"], components=["Authentication"],
               fix_versions=["2.4.0"]),
        _issue("PLAT-3", "Payment retry spikes", "Bug", "Done", 2, "carol", "SPR-2",
               priority="Medium", labels=["checkout", "payments"],
               components=["Checkout"], fix_versions=["2.4.0"]),
        _issue("PLAT-4", "SSE trace stream", "Story", "Done", 8, "alice", "SPR-2",
               priority="Medium", labels=["observability"],
               components=["Platform"], fix_versions=["2.4.0"]),
        _issue("PLAT-5", "Flaky checkout test", "Bug", "In Progress", 3, "bob", "SPR-3",
               priority="Low", labels=["checkout", "qa"], components=["Checkout"],
               updated="2026-06-02"),
        _issue("PLAT-6", "Rate limiter service", "Story", "In Progress", 5, "dave", "SPR-3",
               priority="Medium", labels=["platform"], components=["Platform"],
               updated="2026-06-10"),
        _issue("PLAT-7", "Audit log retention", "Task", "To Do", 2, "erin", "SPR-4",
               priority="Low", labels=["compliance"], components=["Platform"]),
        _issue("PLAT-8", "Webhook signer rotation", "Story", "To Do", 8, "frank", "SPR-4",
               priority="High", labels=["webhooks"], components=["Webhooks"]),
        _issue(
            "PLAT-9", "Stabilize checkout retries", "Bug", "In Progress", 3, "bob", "SPR-6",
            priority="High", reporter="maya",
            labels=["checkout", "payments"], components=["Checkout"],
            fix_versions=["2.5.0"], epic="PLAT-12",
            due_date="2026-09-10", start_date="2026-09-01",
            created="2026-08-28", updated="2026-09-04",
            blocked_by=["PLAT-13"], blocks=["PLAT-11"],
            comments=[
                _comment("maya", "Reproduced on staging: 429s cascade into a retry storm.",
                         date(2026, 9, 2)),
                _comment("bob", "Need the idempotency key from PLAT-13 before I can land this.",
                         date(2026, 9, 4)),
            ],
            changelog=[
                _change("maya", "status", "To Do", "In Progress", date(2026, 9, 1)),
                _change("sarah", "priority", "Medium", "High", date(2026, 9, 3)),
            ],
        ),
        _issue(
            "PLAT-10", "Add sprint health dashboard", "Story", "In Progress", 5, "alice", "SPR-6",
            priority="Medium", labels=["observability"], components=["Platform"],
            fix_versions=["2.5.0"],
            due_date="2026-09-13", start_date="2026-09-01",
            created="2026-08-29", updated="2026-09-05",
            comments=[_comment("tom", "This is the exec-facing view for Friday's review.",
                               date(2026, 9, 3))],
            changelog=[_change("alice", "status", "To Do", "In Progress", date(2026, 9, 1))],
        ),
        _issue(
            "PLAT-11", "Document incident handover", "Task", "To Do", 2, "erin", "SPR-6",
            priority="Medium", labels=["docs"], components=["Platform"],
            fix_versions=["2.5.0"], blocked_by=["PLAT-9"],
            created="2026-08-30", updated="2026-09-01",
        ),
        _issue(
            "PLAT-12", "Checkout reliability", "Epic", "In Progress", 0, "sarah", "backlog",
            priority="High", labels=["checkout"], components=["Checkout"],
            fix_versions=["2.5.0"], created="2026-08-15", updated="2026-09-04",
        ),
        _issue(
            "PLAT-13", "Checkout idempotency key", "Story", "In Progress", 5, "dave", "SPR-6",
            priority="Highest", reporter="sarah",
            labels=["checkout", "payments"], components=["Checkout"],
            fix_versions=["2.5.0"], epic="PLAT-12",
            due_date="2026-09-08", start_date="2026-09-02",
            created="2026-08-31", updated="2026-09-05",
            blocks=["PLAT-9"],
            comments=[_comment("dave", "API contract drafted; waiting on PAY ledger uniqueness.",
                               date(2026, 9, 4))],
            changelog=[_change("dave", "status", "To Do", "In Progress", date(2026, 9, 2))],
        ),
        _issue(
            "PLAT-14", "Webhook signature mismatch", "Bug", "To Do", 3, "unassigned", "SPR-6",
            priority="Critical", reporter="maya",
            labels=["webhooks"], components=["Webhooks"],
            fix_versions=["2.5.0"],
            due_date="2026-09-03", created="2026-09-02", updated="2026-09-02",
            comments=[_comment("maya", "Prod payments are being rejected as unsigned.",
                               date(2026, 9, 2))],
        ),
        _issue(
            "PLAT-15", "Wire sprint burndown chart", "Sub-task", "To Do", 2, "alice", "SPR-6",
            priority="Medium", parent="PLAT-10",
            labels=["observability"], components=["Platform"],
            fix_versions=["2.5.0"], created="2026-09-01", updated="2026-09-01",
        ),
        _issue(
            "PLAT-16", "Feature flag kill-switch for checkout", "Story", "To Do", 3, "carol",
            "SPR-6",
            priority="High", labels=["checkout", "flags"], components=["Checkout"],
            fix_versions=["2.5.0"], epic="PLAT-12",
            created="2026-09-03", updated="2026-09-03",
        ),
        _issue(
            "PLAT-17", "Session store timeout under load", "Bug", "In Review", 3, "frank",
            "SPR-6",
            priority="Medium", labels=["sessions"], components=["Platform"],
            fix_versions=["2.5.0"], created="2026-08-31", updated="2026-09-05",
            changelog=[
                _change("frank", "status", "To Do", "In Progress", date(2026, 9, 1)),
                _change("frank", "status", "In Progress", "In Review", date(2026, 9, 5)),
            ],
        ),
        _issue(
            "PLAT-18", "P95 checkout latency budget", "Story", "To Do", 5, "unassigned",
            "backlog",
            priority="Medium", labels=["checkout"], components=["Checkout"],
            epic="PLAT-12", created="2026-09-04", updated="2026-09-04",
        ),
        _issue(
            "NOVA-1", "Offline-first checkout", "Epic", "In Progress", 0, "leo", "backlog",
            priority="High", labels=["offline"], components=["Checkout"],
            fix_versions=["3.1.0"], created="2026-08-10", updated="2026-09-04",
        ),
        _issue(
            "NOVA-2", "Sync queue for offline carts", "Story", "In Progress", 8, "leo",
            "NOVA-SPR-3",
            priority="High", labels=["offline"], components=["Checkout"],
            fix_versions=["3.1.0"], epic="NOVA-1",
            due_date="2026-09-12", start_date="2026-09-01",
            created="2026-08-28", updated="2026-09-04",
            blocked_by=["NOVA-4"],
            comments=[_comment("leo", "Blocked on the iOS 18 keychain expiry bug.",
                               date(2026, 9, 4))],
        ),
        _issue(
            "NOVA-3", "Biometric login", "Story", "Done", 5, "leo", "NOVA-SPR-2",
            priority="High", labels=["auth"], components=["Authentication"],
            fix_versions=["3.1.0"], created="2026-08-04", updated="2026-08-14",
        ),
        _issue(
            "NOVA-4", "Keychain token expiry on iOS 18", "Bug", "In Progress", 5, "leo",
            "NOVA-SPR-3",
            priority="Highest", labels=["auth", "ios"], components=["Authentication"],
            fix_versions=["3.1.0"],
            due_date="2026-09-09", created="2026-09-01", updated="2026-09-05",
            blocks=["NOVA-2"],
        ),
        _issue(
            "NOVA-5", "App Store screenshots for 3.1", "Task", "To Do", 2, "carol",
            "NOVA-SPR-3",
            priority="Low", labels=["release"], components=["Mobile"],
            due_date="2026-09-01", created="2026-08-30", updated="2026-08-30",
        ),
        _issue(
            "NOVA-6", "Android deep-link crash", "Bug", "To Do", 3, "carol", "NOVA-SPR-3",
            priority="High", labels=["android"], components=["Mobile"],
            fix_versions=["3.1.0"], created="2026-09-03", updated="2026-09-03",
        ),
        _issue(
            "ANLT-1", "Real-time funnel", "Epic", "In Progress", 0, "nina", "backlog",
            priority="Medium", labels=["funnel"], components=["Pipeline"],
            fix_versions=["1.3.0"], created="2026-08-20", updated="2026-09-03",
        ),
        _issue(
            "ANLT-2", "Kafka consumer lag dashboard", "Story", "In Progress", 5, "nina",
            "ANLT-SPR-2",
            priority="High", labels=["observability"], components=["Pipeline"],
            fix_versions=["1.3.0"], epic="ANLT-1",
            created="2026-08-31", updated="2026-09-05",
        ),
        _issue(
            "ANLT-3", "Duplicate pageview events", "Bug", "To Do", 3, "nina", "ANLT-SPR-2",
            priority="Medium", labels=["quality"], components=["Pipeline"],
            fix_versions=["1.3.0"], created="2026-09-02", updated="2026-09-02",
        ),
        _issue(
            "ANLT-4", "Cohort retention report", "Story", "Done", 8, "nina", "ANLT-SPR-1",
            priority="High", labels=["reporting"], components=["Reporting"],
            fix_versions=["1.2.0"], created="2026-08-04", updated="2026-08-15",
        ),
        _issue(
            "PAY-1", "Ledger v2", "Epic", "In Progress", 0, "priya", "backlog",
            priority="High", labels=["ledger"], components=["Ledger"],
            fix_versions=["1.8.0"], created="2026-08-12", updated="2026-09-04",
        ),
        _issue(
            "PAY-2", "Double-entry posting", "Story", "In Progress", 8, "priya", "PAY-SPR-2",
            priority="High", labels=["ledger"], components=["Ledger"],
            fix_versions=["1.8.0"], epic="PAY-1",
            due_date="2026-09-11", created="2026-08-28", updated="2026-09-05",
            blocks=["PAY-4"],
        ),
        _issue(
            "PAY-3", "Settlement file timezone drift", "Bug", "To Do", 5, "priya",
            "PAY-SPR-2",
            priority="Highest", labels=["settlement"], components=["Settlements"],
            fix_versions=["1.8.0"],
            due_date="2026-09-02", created="2026-08-29", updated="2026-09-01",
            comments=[_comment("tom", "Finance cannot close August until this lands.",
                               date(2026, 9, 1))],
        ),
        _issue(
            "PAY-4", "Refunds idempotency", "Story", "To Do", 5, "dave", "PAY-SPR-2",
            priority="High", labels=["refunds"], components=["Ledger"],
            fix_versions=["1.8.0"], epic="PAY-1",
            blocked_by=["PAY-2"], created="2026-08-31", updated="2026-09-02",
        ),
    ]

    worklogs = {
        "PLAT-1": [
            {"author": "alice", "hours": 6.0, "date": "2026-08-02",
             "comment": "implementation"},
        ],
        "PLAT-9": [
            {"author": "bob", "hours": 4.0, "date": "2026-09-02",
             "comment": "reproduced retry storm and added metrics"},
            {"author": "bob", "hours": 3.5, "date": "2026-09-04",
             "comment": "partial backoff; still blocked on idempotency"},
        ],
        "PLAT-10": [
            {"author": "alice", "hours": 5.0, "date": "2026-09-03",
             "comment": "dashboard shell and status tiles"},
        ],
        "PLAT-13": [
            {"author": "dave", "hours": 6.0, "date": "2026-09-03",
             "comment": "idempotency key design"},
        ],
        "NOVA-2": [
            {"author": "leo", "hours": 7.0, "date": "2026-09-02",
             "comment": "offline queue prototype"},
        ],
        "PAY-2": [
            {"author": "priya", "hours": 8.0, "date": "2026-09-03",
             "comment": "posting journal"},
        ],
    }

    return {
        "users": {k: dict(v) for k, v in USERS.items()},
        "projects": {k: {**v, "members": list(v["members"])} for k, v in PROJECTS.items()},
        "boards": {k: dict(v) for k, v in BOARDS.items()},
        "sprints": sprints,
        "versions": versions,
        "issues": issues,
        "worklogs": worklogs,
    }
