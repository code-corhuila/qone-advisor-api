"""The agent's tools against the seeded projections: closed over the caller (INV-ADV-002), read
only (INV-ADV-001), and the proposal accepted only when the saga's gates would pass."""

from uuid import UUID

from advisor.application.usecase.tools import AdvisorTools
from advisor.domain.model.conversation import UserRole
from tests.fakes import CARLOS, ISW_602_G1, ISW_604_G1, ISW_604_G2, ISW_604_G3, LAURA, SANTIAGO, seeded_advisor


def tools(user=LAURA, role=UserRole.STUDENT) -> AdvisorTools:
    return AdvisorTools(user, role, seeded_advisor(), "2026-B")


def by_name(t: AdvisorTools) -> dict:
    return {tool.name: tool for tool in t.for_role()}


def test_a_student_gets_six_tools_and_none_takes_a_user_id():
    names = by_name(tools())
    assert list(names) == [
        "degree_audit",
        "my_schedule",
        "list_sections",
        "tuition_status",
        "check_section",
        "propose_enrollment",
    ]
    for tool in names.values():
        assert tool.parameters["type"] == "object" and tool.description
        assert not any("student" in p or "user" in p for p in tool.parameters.get("properties", {}))


def test_professors_and_admins_only_list_sections():
    for role in (UserRole.PROFESSOR, UserRole.ADMIN):
        assert list(by_name(tools(CARLOS, role))) == ["list_sections"]


def test_the_audit_and_the_schedule_are_the_callers_own():
    t = tools()
    audit = t.degree_audit({})
    assert {s["code"] for s in audit["subjects"]} == {"ISW-501", "RED-501", "ISW-601", "ISW-602", "ISW-604"}
    assert audit["credits"] == {"total": 16, "passed": 6, "inProgress": 3}
    schedule = t.my_schedule({})
    assert schedule == [
        {
            "subjectCode": "ISW-601",
            "subjectName": "Bases de Datos II",
            "groupNumber": 1,
            "slots": [
                {"day": "TUE", "start": "08:00", "end": "10:00"},
                {"day": "THU", "start": "08:00", "end": "10:00"},
            ],
        }
    ]
    assert tools(SANTIAGO).tuition_status({}) == {"term": "2026-B", "status": "ISSUED"}
    assert tools(UUID(int=404)).tuition_status({}) == {"term": "2026-B", "status": "UNKNOWN"}


def test_the_listing_marks_what_would_block_each_section_for_a_student():
    listed = {s["groupNumber"]: s["blockers"] for s in tools().list_sections({"subject": "ISW-604"})}
    assert listed == {1: [], 2: ["SCHEDULE_CONFLICT"], 3: ["NO_SEATS"]}
    assert len(tools().list_sections({})) == 5


def test_check_section_repeats_the_gates():
    t = tools()
    ok = t.check_section({"section_id": str(ISW_604_G1)})
    assert ok["canEnroll"] and ok["section"]["seatsAvailable"] == 27
    blocked = t.check_section({"section_id": str(ISW_604_G2)})
    assert blocked["blockers"] == ["SCHEDULE_CONFLICT"] and blocked["conflictsWith"] == ["ISW-601"]
    assert tools(SANTIAGO).check_section({"section_id": str(ISW_604_G3)})["blockers"] == [
        "TUITION_UNPAID",
        "PREREQ_MISSING",
        "NO_SEATS",
    ]
    assert "error" in t.check_section({"section_id": "not-a-uuid"})
    assert "error" in t.check_section({"section_id": str(UUID(int=7))})


def test_a_proposal_is_kept_only_when_every_gate_passes():
    t = tools()
    assert "rechazaría" in t.propose_enrollment({"section_id": str(ISW_604_G3), "reason": "x"}) and t.proposal is None
    assert "error" in t.propose_enrollment({"section_id": str(ISW_602_G1), "reason": " "}) and t.proposal is None
    answer = t.propose_enrollment({"section_id": str(ISW_602_G1), "reason": "encaja en tus tardes"})
    assert "confirme" in answer and t.proposal.section.section_id == ISW_602_G1
    assert t.proposal.reason == "encaja en tus tardes"
