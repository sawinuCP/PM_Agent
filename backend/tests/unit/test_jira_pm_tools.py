"""Unit tests for the local Jira project-management service (no LLM, no network)."""

from __future__ import annotations

import pytest
from mcp_servers.jira import service as svc
from mcp_servers.jira.dataset import AS_OF


@pytest.fixture(autouse=True)
def _fresh_world() -> None:
    svc.reset_state()
    yield
    svc.reset_state()


def test_seed_preserves_platform_contract() -> None:
    plat9 = svc.get_issue("PLAT-9")["issue"]
    assert plat9["summary"] == "Stabilize checkout retries"
    assert plat9["type"] == "Bug"
    assert plat9["status"] == "In Progress"
    assert plat9["story_points"] == 3
    assert plat9["assignee"] == "bob"
    assert plat9["sprint"] == "SPR-6"

    closed = svc.get_sprints("BRD-1", "closed")
    assert [s["id"] for s in closed["sprints"]] == [f"SPR-{i}" for i in range(1, 6)]
    assert [s["completed_points"] for s in closed["sprints"]] == [30, 42, 38, 45, 36]

    active = svc.get_sprints("BRD-1", "active")
    assert active["sprints"][0]["id"] == "SPR-6"
    assert active["sprints"][0]["committed_points"] == 24
    assert active["sprints"][0]["completed_points"] == 8


def test_search_active_sprint_includes_original_tickets() -> None:
    result = svc.search_issues("sprint = SPR-6")
    ids = {i["id"] for i in result["issues"]}
    assert {"PLAT-9", "PLAT-10", "PLAT-11"} <= ids
    assert result["total_matched"] >= 3


def test_jql_priority_and_unknown_field() -> None:
    high = svc.search_issues("project = PLAT AND priority = Critical")
    assert any(i["id"] == "PLAT-14" for i in high["issues"])
    bad = svc.search_issues("foo = bar")
    assert "error" in bad


def test_unknown_identifiers() -> None:
    assert "error" in svc.get_issue("PROJ-999")
    assert "error" in svc.get_sprint("SPR-999")
    assert "error" in svc.get_project("ZZZZ")
    assert "error" in svc.get_board("BRD-99")
    assert "error" in svc.get_release_status("PLAT", "9.9.9")
    assert svc.get_issue("PROJ-999")["error"] == "issue 'PROJ-999' not found"


def test_empty_and_invalid_inputs() -> None:
    assert "error" in svc.get_issue("")
    assert "error" in svc.search_issues("")
    assert "error" in svc.create_issue("PLAT", "Mystery", "x")
    assert "error" in svc.create_issue("NOPE", "Bug", "x")
    illegal = svc.transition_issue("PLAT-11", "Done")
    assert "illegal transition" in illegal["error"]


def test_search_resolves_display_name_and_open_status() -> None:
    alice = svc.search_issues('assignee = "Alice Chen"')
    assert alice["total_matched"] >= 1
    assert any(i["assignee"] == "alice" for i in alice["issues"])
    opened = svc.search_issues("status = Open AND type = Bug")
    assert opened["total_matched"] >= 1
    assert all(i["status"] != "Done" for i in opened["issues"])
    texted = svc.search_issues('summary = "webhook signature"')
    assert any(i["id"] == "PLAT-14" for i in texted["issues"])
    any_active = svc.get_sprints(state="active")
    assert any_active["count"] >= 1


def test_projects_and_name_lookup() -> None:
    listing = svc.get_projects()
    keys = {p["key"] for p in listing["projects"]}
    assert keys == {"PLAT", "NOVA", "ANLT", "PAY"}
    nova = svc.get_project("Nova Mobile")
    assert nova["project"]["key"] == "NOVA"
    assert nova["active_sprint"]["id"] == "NOVA-SPR-3"


def test_blocked_and_dependency_graph() -> None:
    blocked = svc.get_blocked_issues("PLAT")
    ids = {i["id"] for i in blocked["issues"]}
    assert "PLAT-9" in ids
    assert "PLAT-11" in ids
    deps = svc.get_dependencies("PLAT-9")
    assert any(i["id"] == "PLAT-13" for i in deps["blocked_by"])
    assert any(i["id"] == "PLAT-11" for i in deps["blocks"])


def test_overdue_uses_pinned_clock() -> None:
    overdue = svc.get_overdue_issues("PLAT")
    assert overdue["as_of"] == AS_OF.isoformat()
    assert any(i["id"] == "PLAT-14" for i in overdue["issues"])
    assert all(i["is_overdue"] for i in overdue["issues"])


def test_release_status_is_derived() -> None:
    status = svc.get_release_status("PLAT", "2.5.0")
    assert status["release"]["name"] == "2.5.0"
    assert status["issue_counts"]["open"] >= 1
    assert any(i["id"] == "PLAT-9" for i in status["open_issues"])
    assert 0 <= status["completion_percent"] <= 100


def test_project_health_is_derived_not_invented() -> None:
    health = svc.get_project_health("PLAT")
    assert health["assessment"] == "derived_from_project_data"
    assert health["dimensions"]["overall"] in {"Healthy", "Elevated", "At Risk"}
    assert health["evidence"]["blocked_issues"] == svc.get_blocked_issues("PLAT")["count"]
    assert health["evidence"]["overdue_issues"] == svc.get_overdue_issues("PLAT")["count"]
    assert health["evidence"]["active_sprint"] == "SPR-6"


def test_sprint_report_flags_scope_creep_and_blockers() -> None:
    report = svc.get_sprint_report("SPR-6")
    added = {i["id"] for i in report["added_after_start"]}
    assert "PLAT-16" in added
    assert any(i["id"] == "PLAT-9" for i in report["blocked"])
    assert report["days_remaining"] == 9


def test_workload_and_epics() -> None:
    load = svc.get_team_workload("PLAT", "SPR-6")
    assignees = {row["assignee"] for row in load["workload"]}
    assert "bob" in assignees
    bob = next(row for row in load["workload"] if row["assignee"] == "bob")
    assert "PLAT-9" in bob["issue_keys"]
    epics = svc.get_epics("PLAT")
    assert any(e["id"] == "PLAT-12" for e in epics["epics"])
    checkout = next(e for e in epics["epics"] if e["id"] == "PLAT-12")
    child_ids = {c["id"] for c in checkout["children"]}
    assert "PLAT-9" in child_ids


def test_comments_history_worklogs_activity() -> None:
    comments = svc.get_issue_comments("PLAT-9")
    assert comments["count"] >= 2
    history = svc.get_issue_history("PLAT-9")
    assert any(c["field"] == "status" for c in history["changelog"])
    logs = svc.get_worklogs("PLAT-9")
    assert logs["total_hours"] == 7.5
    activity = svc.get_recent_activity("PLAT", days=7)
    assert activity["count"] >= 1
    assert any(e["issue_id"] == "PLAT-9" for e in activity["activity"])


def test_writes_mutate_and_reset_restores() -> None:
    created = svc.create_issue("NOVA", "Bug", "Crash on tablet rotate")
    assert created["created"] is True
    new_id = created["issue"]["id"]
    found = svc.search_issues("project = NOVA AND type = Bug")
    assert any(i["id"] == new_id for i in found["issues"])
    svc.reset_fixtures()
    assert "error" in svc.get_issue(new_id)


def test_pagination_does_not_return_everything() -> None:
    page = svc.search_issues("project = PLAT", max_results=2, start_at=0)
    assert page["count"] == 2
    assert page["has_more"] is True
    assert page["total_matched"] > 2


def test_sprint_issues_batch_last_n() -> None:
    batch = svc.get_sprint_issues(last_n=11)
    assert "error" not in batch
    assert batch["totals"]["sprints"] >= 3
    assert len(batch["by_sprint"]) == batch["totals"]["sprints"]
    assert "bug_count" in batch["by_sprint"][0]
    assert batch["totals"]["bugs"] >= 0
    eleven = svc.get_sprint_issues(last_n=11, issue_type="Bug")
    assert eleven["issue_type"] == "Bug"
    assert all(
        all(issue.get("type") == "Bug" for issue in row["issues"])
        for row in eleven["by_sprint"]
    )


def test_sprint_report_batch_comparison() -> None:
    report = svc.get_sprint_report(last_n=5)
    assert "error" not in report
    assert report["count"] >= 3
    assert len(report["comparison"]["bug_counts"]) == report["count"]


def test_sprints_include_metrics() -> None:
    rows = svc.get_sprints("BRD-1", "closed", last_n=5, include_metrics=True)
    assert rows["count"] == 5
    assert rows["sprints"][-1]["bug_count"] >= 0
    assert "type_counts" in rows["sprints"][-1]


def test_issue_batch_lookup() -> None:
    batch = svc.get_issue(issue_ids="PLAT-9,PLAT-10")
    assert batch["count"] == 2
    assert {row["id"] for row in batch["issues"]} == {"PLAT-9", "PLAT-10"}


def test_project_and_board_batch_lookup() -> None:
    projects = svc.get_project(projects="PLAT,NOVA")
    assert projects["count"] == 2
    assert set(projects["projects"]) == {"PLAT", "NOVA"}
    assert all(row.get("issue_counts") for row in projects["items"])
    boards = svc.get_board(board_ids="BRD-1,BRD-2")
    assert boards["count"] == 2
    assert {row["id"] for row in boards["boards"]} == {"BRD-1", "BRD-2"}
    single = svc.get_project("PLAT")
    assert single["project"]["key"] == "PLAT"
    assert "items" not in single


def test_comma_packed_singular_id_fields() -> None:
    """LLMs often put comma-separated ids in the singular arg — service must parse."""
    boards = svc.get_board(board_id="BRD-1,BRD-2,BRD-3,BRD-4")
    assert boards["count"] == 4
    sprints = svc.get_sprints(board_id="BRD-1,BRD-2,BRD-3,BRD-4", state="closed")
    assert sprints["count"] >= 8
    assert all(s["board_id"] in {"BRD-1", "BRD-2", "BRD-3", "BRD-4"} for s in sprints["sprints"])
    batch = svc.get_sprint(sprint_id="SPR-1,SPR-2")
    assert batch["count"] == 2
    issues = svc.get_issue(issue_ids="PLAT-9,PLAT-10")
    assert issues["count"] == 2


def test_health_blocked_and_version_batches() -> None:
    health = svc.get_project_health(projects="PLAT,NOVA")
    assert health["count"] == 2
    assert health["totals"]["projects"] == 2
    assert {row["project"] for row in health["projects"]} == {"PLAT", "NOVA"}
    blocked = svc.get_blocked_issues(projects="PLAT,NOVA,PAY")
    assert blocked["totals"]["projects"] == 3
    assert len(blocked["by_project"]) == 3
    versions = svc.get_release_status(versions="2.5.0,3.1.0")
    assert versions["count"] == 2
    assert "totals" in versions
    names = {row["release"]["name"] for row in versions["items"]}
    assert names == {"2.5.0", "3.1.0"}
    deps = svc.get_dependencies(issue_ids="PLAT-9,NOVA-2")
    assert deps["count"] == 2
    assert set(deps["issue_ids"]) == {"PLAT-9", "NOVA-2"}


def test_dispatch_unknown_tool() -> None:
    assert "error" in svc.dispatch("jira_get_everything")


def test_cross_project_blockers_exist() -> None:
    nova = svc.get_blocked_issues("NOVA")
    assert any(i["id"] == "NOVA-2" for i in nova["issues"])
    pay = svc.get_overdue_issues("PAY")
    assert any(i["id"] == "PAY-3" for i in pay["issues"])
