"""Test doubles: a clock that can move, ids in sequence, a scripted agent that calls the real tools,
a saga client that answers what the test says, and the in-memory advisor seeded with a slice of
the demo (same ids as the -db seeds)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from advisor.adapter.outbound.persistence.memory import InMemoryAdvisor
from advisor.application.port.outbound.ports import AgentRequest, AgentUnavailable, SagaResult, SagaUnavailable
from advisor.domain.model.advising import ConfirmedSection, SectionView, StudentView, SubjectView
from advisor.domain.model.slot import Slot

LAURA = UUID("11111111-1111-4111-8111-111111111111")
SANTIAGO = UUID("11111111-1111-4111-8111-111111111112")
CARLOS = UUID("22222222-2222-4222-8222-222222222221")
ISW_601_G1 = UUID("55555555-5555-4555-8555-555555555501")
ISW_604_G1 = UUID("55555555-5555-4555-8555-555555555505")
ISW_604_G2 = UUID("55555555-5555-4555-8555-555555555506")
ISW_604_G3 = UUID("55555555-5555-4555-8555-555555555507")
ISW_602_G1 = UUID("55555555-5555-4555-8555-555555555510")


class MovableClock:
    def __init__(self) -> None:
        self.at = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at

    def advance(self, minutes: float) -> None:
        self.at += timedelta(minutes=minutes)


class SequenceIds:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> UUID:
        self.n += 1
        return UUID(f"99999999-9999-4999-8999-{self.n:012d}")


class ScriptedAgent:
    """Calls the tools the script names, in order, then answers `text`."""

    def __init__(self, calls: list[tuple[str, dict]] | None = None, text: str = "Listo.") -> None:
        self.calls = calls or []
        self.text = text
        self.requests: list[AgentRequest] = []
        self.results: list[object] = []

    @property
    def model(self) -> str:
        return "scripted-model"

    def reply(self, request: AgentRequest) -> str:
        self.requests.append(request)
        tools = {t.name: t for t in request.tools}
        for name, args in self.calls:
            self.results.append(tools[name].run(args) if name in tools else f"unknown tool {name}")
        return self.text


class DownAgent:
    model = "scripted-model"

    def reply(self, request: AgentRequest) -> str:
        raise AgentUnavailable("circuit open")


class FakeSaga:
    def __init__(self, status: str = "COMPLETED", reason: str | None = None) -> None:
        self.status = status
        self.reason = reason
        self.down = False
        self.calls: list[tuple] = []
        self.on_start: Callable[[], None] | None = None

    def start(self, student_id, section_id, confirmation_id, idempotency_key, correlation_id) -> SagaResult:
        self.calls.append((student_id, section_id, confirmation_id, idempotency_key, correlation_id))
        if self.down:
            raise SagaUnavailable()
        return SagaResult(UUID("77777777-7777-4777-8777-777777777799"), self.status, self.reason)


def slots(*triples: tuple[str, str, str]) -> tuple[Slot, ...]:
    return tuple(Slot.parse(*t) for t in triples)


TUE_THU_8 = slots(("TUE", "08:00", "10:00"), ("THU", "08:00", "10:00"))


def seeded_advisor() -> InMemoryAdvisor:
    store = InMemoryAdvisor()
    for subject in (
        SubjectView("ISW-501", "Bases de Datos I", 3, 5),
        SubjectView("RED-501", "Redes de Datos", 3, 5),
        SubjectView("ISW-601", "Bases de Datos II", 3, 6, ("ISW-501",)),
        SubjectView("ISW-602", "Teoría de la Computación", 3, 6),
        SubjectView("ISW-604", "Sistemas Distribuidos", 4, 6, ("RED-501",)),
    ):
        store.subjects[subject.code] = subject
    for section in (
        SectionView(ISW_601_G1, "ISW-601", "Bases de Datos II", "2026-B", 1, "Ana Torres", TUE_THU_8, 29),
        SectionView(
            ISW_604_G1,
            "ISW-604",
            "Sistemas Distribuidos",
            "2026-B",
            1,
            "Carlos Ramírez",
            slots(("MON", "08:00", "10:00"), ("WED", "08:00", "10:00")),
            27,
        ),
        SectionView(ISW_604_G2, "ISW-604", "Sistemas Distribuidos", "2026-B", 2, "Ana Torres", TUE_THU_8, 30),
        SectionView(
            ISW_604_G3,
            "ISW-604",
            "Sistemas Distribuidos",
            "2026-B",
            3,
            "Jorge Medina",
            slots(("FRI", "08:00", "12:00")),
            0,
        ),
        SectionView(
            ISW_602_G1,
            "ISW-602",
            "Teoría de la Computación",
            "2026-B",
            1,
            "Ana Torres",
            slots(("TUE", "16:00", "18:00"), ("THU", "16:00", "18:00")),
            30,
        ),
    ):
        store.section_views[section.section_id] = section
    store.put_student(
        StudentView(
            LAURA, frozenset({"ISW-501", "RED-501"}), (ConfirmedSection(ISW_601_G1, "ISW-601", TUE_THU_8),), "PAID"
        )
    )
    store.put_student(StudentView(SANTIAGO, frozenset({"ISW-501"}), (), "ISSUED"))
    return store
