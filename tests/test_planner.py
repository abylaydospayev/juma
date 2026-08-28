from juma.planner import build_plan


def test_coding_change_plan_includes_approval_and_checks() -> None:
    plan = build_plan("add a health endpoint", "coding")

    assert plan[0].startswith("Inspect")
    assert any("focused tests" in step for step in plan)
    assert any("approval" in step for step in plan)


def test_research_plan_is_evidence_oriented() -> None:
    plan = build_plan("research durable memory", "research")

    assert any("evidence" in step for step in plan)
    assert any("sources" in step for step in plan)
