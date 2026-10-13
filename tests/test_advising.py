"""The degree audit over the pensum projection (HU-ADV-002) and the assessment that repeats the
saga's gates in the saga's order (HU-ADV-003), with the demo's data."""

from uuid import UUID

from advisor.domain.model.advising import (
    Blocker,
    ConfirmedSection,
    SectionView,
    StudentView,
    SubjectState,
    SubjectView,
    assess,
    degree_audit,
)
from advisor.domain.model.slot import Slot

LAURA = UUID("11111111-1111-4111-8111-111111111111")
PENSUM = [
    SubjectView("ISW-501", "Bases de Datos I", 3, 5),
    SubjectView("RED-501", "Redes de Datos", 3, 5),
    SubjectView("ISW-601", "Bases de Datos II", 3, 6, ("ISW-501",)),
    SubjectView("ISW-604", "Sistemas Distribuidos", 4, 6, ("RED-501",)),
    SubjectView("ISW-701", "Seguridad Informática", 3, 7, ("ISW-604",)),
]
TUE_THU_8 = (Slot.parse("TUE", "08:00", "10:00"), Slot.parse("THU", "08:00", "10:00"))
MON_WED_8 = (Slot.parse("MON", "08:00", "10:00"), Slot.parse("WED", "08:00", "10:00"))


def section(code: str, slots: tuple[Slot, ...], seats: int = 10, n: int = 1) -> SectionView:
    return SectionView(UUID(int=n), code, code, "2026-B", 1, "Ana Torres", slots, seats)


def laura(tuition: str | None = "PAID") -> StudentView:
    return StudentView(
        LAURA, frozenset({"ISW-501", "RED-501"}), (ConfirmedSection(UUID(int=50), "ISW-601", TUE_THU_8),), tuition
    )


def test_the_audit_classifies_every_subject_of_the_pensum_and_nothing_else():
    audit = degree_audit(laura(), PENSUM)
    states = {a.subject.code: a.state for a in audit.subjects}
    assert states == {
        "ISW-501": SubjectState.PASSED,
        "RED-501": SubjectState.PASSED,
        "ISW-601": SubjectState.IN_PROGRESS,
        "ISW-604": SubjectState.AVAILABLE,
        "ISW-701": SubjectState.LOCKED,
    }
    locked = next(a for a in audit.subjects if a.subject.code == "ISW-701")
    assert locked.missing_prerequisites == ("ISW-604",)
    assert (audit.credits_total, audit.credits_passed, audit.credits_in_progress, audit.percent) == (16, 6, 3, 38)
    assert [a.subject.code for a in audit.pending] == ["ISW-604", "ISW-701"]


def test_an_empty_pensum_audits_to_zero_percent():
    assert degree_audit(laura(), []).percent == 0


def test_a_section_that_passes_every_gate_can_be_proposed():
    verdict = assess(section("ISW-604", MON_WED_8), PENSUM[3], laura())
    assert verdict.can_enroll and verdict.blockers == ()


def test_the_blockers_follow_the_saga_order():
    student = StudentView(LAURA, frozenset(), (ConfirmedSection(UUID(int=50), "ISW-601", TUE_THU_8),), "ISSUED")
    verdict = assess(section("ISW-604", TUE_THU_8, seats=0), PENSUM[3], student)
    assert verdict.blockers == (
        Blocker.TUITION_UNPAID,
        Blocker.PREREQ_MISSING,
        Blocker.SCHEDULE_CONFLICT,
        Blocker.NO_SEATS,
    )
    assert verdict.missing_prerequisites == ("RED-501",)
    assert verdict.conflicts_with == ("ISW-601",)


def test_an_active_duplicate_blocks_and_is_not_its_own_conflict():
    verdict = assess(section("ISW-601", TUE_THU_8, n=2), PENSUM[2], laura())
    assert verdict.blockers == (Blocker.DUPLICATE,)
    assert verdict.conflicts_with == ()


def test_unknown_tuition_is_not_paid():
    assert Blocker.TUITION_UNPAID in assess(section("ISW-604", MON_WED_8), PENSUM[3], laura(None)).blockers


def test_contiguous_classes_do_not_conflict():
    after = (Slot.parse("TUE", "10:00", "12:00"),)
    assert assess(section("ISW-604", after), PENSUM[3], laura()).can_enroll


def test_the_student_view_tracks_confirmed_sections_by_id():
    view = laura().with_confirmed(ConfirmedSection(UUID(int=51), "ISW-604", MON_WED_8))
    assert [c.subject_code for c in view.confirmed] == ["ISW-601", "ISW-604"]
    again = view.with_confirmed(ConfirmedSection(UUID(int=51), "ISW-604", MON_WED_8))
    assert len(again.confirmed) == 2
    assert [c.subject_code for c in again.without_section(UUID(int=50)).confirmed] == ["ISW-604"]
