"""What the agent knows, read from its projections (D-11), and the two judgments it makes over
them: the degree audit (HU-ADV-002) and whether a section would pass the enrollment gates
(HU-ADV-003). The assessment repeats the saga's gates in the saga's order so the agent never
proposes what the saga would reject: tuition, an active duplicate, a missing prerequisite, a
schedule conflict, no seats (ADR-008). The saga still decides; this only avoids bad proposals."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID

from advisor.domain.model.slot import Slot


@dataclass(frozen=True)
class SubjectView:
    code: str
    name: str
    credits: int
    semester: int
    prerequisites: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionView:
    section_id: UUID
    subject_code: str
    subject_name: str
    term: str
    group_number: int
    professor_name: str
    slots: tuple[Slot, ...]
    seats_available: int


@dataclass(frozen=True)
class ConfirmedSection:
    section_id: UUID
    subject_code: str
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class StudentView:
    student_id: UUID
    passed_codes: frozenset[str] = frozenset()
    confirmed: tuple[ConfirmedSection, ...] = ()
    tuition_status: str | None = None

    def with_confirmed(self, section: ConfirmedSection) -> "StudentView":
        kept = tuple(c for c in self.confirmed if c.section_id != section.section_id)
        return replace(self, confirmed=kept + (section,))

    def without_section(self, section_id: UUID) -> "StudentView":
        return replace(self, confirmed=tuple(c for c in self.confirmed if c.section_id != section_id))


class SubjectState(StrEnum):
    PASSED = "PASSED"
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class AuditedSubject:
    subject: SubjectView
    state: SubjectState
    missing_prerequisites: tuple[str, ...]


@dataclass(frozen=True)
class Audit:
    subjects: tuple[AuditedSubject, ...]
    credits_total: int
    credits_passed: int
    credits_in_progress: int
    percent: int

    @property
    def pending(self) -> tuple[AuditedSubject, ...]:
        return tuple(s for s in self.subjects if s.state in (SubjectState.AVAILABLE, SubjectState.LOCKED))


def degree_audit(student: StudentView, pensum: list[SubjectView]) -> Audit:
    """PASSED in the record; IN_PROGRESS confirmed this term; AVAILABLE when every prerequisite is
    passed; LOCKED otherwise. Only subjects of the pensum projection appear (scenario 1)."""
    in_progress = {c.subject_code for c in student.confirmed}
    items: list[AuditedSubject] = []
    for subject in sorted(pensum, key=lambda s: (s.semester, s.code)):
        missing = tuple(p for p in subject.prerequisites if p not in student.passed_codes)
        if subject.code in student.passed_codes:
            state = SubjectState.PASSED
        elif subject.code in in_progress:
            state = SubjectState.IN_PROGRESS
        elif not missing:
            state = SubjectState.AVAILABLE
        else:
            state = SubjectState.LOCKED
        items.append(AuditedSubject(subject, state, missing))
    total = sum(i.subject.credits for i in items)
    passed = sum(i.subject.credits for i in items if i.state is SubjectState.PASSED)
    current = sum(i.subject.credits for i in items if i.state is SubjectState.IN_PROGRESS)
    return Audit(tuple(items), total, passed, current, round(passed * 100 / total) if total else 0)


class Blocker(StrEnum):
    TUITION_UNPAID = "TUITION_UNPAID"
    DUPLICATE = "DUPLICATE"
    PREREQ_MISSING = "PREREQ_MISSING"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
    NO_SEATS = "NO_SEATS"


@dataclass(frozen=True)
class Assessment:
    blockers: tuple[Blocker, ...] = ()
    missing_prerequisites: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = field(default=())

    @property
    def can_enroll(self) -> bool:
        return not self.blockers


def assess(section: SectionView, subject: SubjectView | None, student: StudentView) -> Assessment:
    blockers: list[Blocker] = []
    if student.tuition_status != "PAID":
        blockers.append(Blocker.TUITION_UNPAID)
    if any(c.subject_code == section.subject_code for c in student.confirmed):
        blockers.append(Blocker.DUPLICATE)
    missing = tuple(p for p in (subject.prerequisites if subject else ()) if p not in student.passed_codes)
    if missing:
        blockers.append(Blocker.PREREQ_MISSING)
    conflicts = tuple(
        c.subject_code
        for c in student.confirmed
        if c.subject_code != section.subject_code and any(m.overlaps(t) for m in section.slots for t in c.slots)
    )
    if conflicts:
        blockers.append(Blocker.SCHEDULE_CONFLICT)
    if section.seats_available <= 0:
        blockers.append(Blocker.NO_SEATS)
    return Assessment(tuple(blockers), missing, conflicts)
