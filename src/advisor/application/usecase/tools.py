"""The agent's tools (ADR-003), closed over the verified caller (INV-ADV-002): no tool takes a user
id, so the model cannot read or act for anyone else. They read the projections only. The one that
"proposes" keeps the proposal in memory for the use case to turn into a PROPOSED plan; no tool
writes anything (INV-ADV-001). A STUDENT gets the six tools; a PROFESSOR or an ADMIN only the
section listing, which carries no student data. Descriptions are in Spanish: the agent talks to
the students in Spanish."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from advisor.application.port.outbound.ports import Projections, Tool
from advisor.domain.model.advising import SectionView, assess, degree_audit
from advisor.domain.model.conversation import UserRole
from advisor.domain.model.slot import Slot

NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
SECTION_ARG: dict[str, Any] = {
    "type": "object",
    "properties": {
        "section_id": {"type": "string", "description": "id de la sección (UUID) tal como lo da list_sections"}
    },
    "required": ["section_id"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Proposal:
    section: SectionView
    reason: str


def slots_of(slots: tuple[Slot, ...]) -> list[dict[str, str]]:
    return [
        {"day": s.day.value, "start": s.start.strftime("%H:%M"), "end": s.end.strftime("%H:%M")} for s in sorted(slots)
    ]


class AdvisorTools:
    def __init__(self, user_id: UUID, role: UserRole, projections: Projections, term: str) -> None:
        self._user_id = user_id
        self._role = role
        self._projections = projections
        self._term = term
        self.proposal: Proposal | None = None

    def for_role(self) -> list[Tool]:
        listing = Tool(
            "list_sections",
            "Grupos ofertados en el periodo: profesor, horario y cupos. `subject` filtra por código (ISW-602) o "
            "parte del nombre; vacío lista todos. Para un estudiante incluye si pasaría las compuertas de matrícula.",
            {"type": "object", "properties": {"subject": {"type": "string"}}, "additionalProperties": False},
            self.list_sections,
        )
        if self._role is not UserRole.STUDENT:
            return [listing]
        return [
            Tool(
                "degree_audit",
                "Pensum del estudiante: cada materia con su estado (PASSED aprobada, IN_PROGRESS cursando, AVAILABLE "
                "disponible, LOCKED bloqueada por prerrequisitos) y los créditos totales, aprobados y en curso.",
                NO_ARGS,
                self.degree_audit,
            ),
            Tool(
                "my_schedule",
                "Materias confirmadas del estudiante este periodo, con días y horas.",
                NO_ARGS,
                self.my_schedule,
            ),
            listing,
            Tool(
                "tuition_status",
                "Estado del pago de matrícula del periodo (PAID habilita matricular).",
                NO_ARGS,
                self.tuition_status,
            ),
            Tool(
                "check_section",
                "Verifica con las mismas reglas de la matrícula si el estudiante puede inscribir una sección: pago, "
                "materia ya inscrita, prerrequisitos, cruce de horario y cupos. Úsala antes de proponer.",
                SECTION_ARG,
                self.check_section,
            ),
            Tool(
                "propose_enrollment",
                "Propone matricular una sección. NO matricula: crea un plan que el estudiante confirma o descarta en "
                "la interfaz y que vence a los 10 minutos. Solo se acepta si check_section no encuentra bloqueos.",
                {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string"},
                        "reason": {"type": "string", "description": "por qué propones esta sección, en una frase"},
                    },
                    "required": ["section_id", "reason"],
                    "additionalProperties": False,
                },
                self.propose_enrollment,
            ),
        ]

    # --- the tools
    def degree_audit(self, _args: dict[str, Any]) -> dict[str, Any]:
        audit = degree_audit(self._projections.student(self._user_id), self._projections.pensum())
        return {
            "subjects": [
                {
                    "code": a.subject.code,
                    "name": a.subject.name,
                    "credits": a.subject.credits,
                    "semester": a.subject.semester,
                    "state": a.state.value,
                    "missingPrerequisites": list(a.missing_prerequisites),
                }
                for a in audit.subjects
            ],
            "credits": {
                "total": audit.credits_total,
                "passed": audit.credits_passed,
                "inProgress": audit.credits_in_progress,
            },
            "percent": audit.percent,
        }

    def my_schedule(self, _args: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for confirmed in self._projections.student(self._user_id).confirmed:
            section = self._projections.section(confirmed.section_id)
            out.append(
                {
                    "subjectCode": confirmed.subject_code,
                    "subjectName": section.subject_name if section else confirmed.subject_code,
                    "groupNumber": section.group_number if section else None,
                    "slots": slots_of(confirmed.slots),
                }
            )
        return out

    def list_sections(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(args.get("subject") or "").strip() or None
        student = self._projections.student(self._user_id) if self._role is UserRole.STUDENT else None
        out = []
        for section in self._projections.sections(self._term, query):
            item: dict[str, Any] = self._describe(section)
            if student is not None:
                verdict = assess(section, self._projections.subject(section.subject_code), student)
                item["blockers"] = [b.value for b in verdict.blockers]
            out.append(item)
        return out

    def tuition_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        status = self._projections.student(self._user_id).tuition_status
        return {"term": self._term, "status": status or "UNKNOWN"}

    def check_section(self, args: dict[str, Any]) -> dict[str, Any]:
        section = self._section(args)
        if section is None:
            return {"error": "la sección no existe en el periodo actual"}
        verdict = assess(
            section, self._projections.subject(section.subject_code), self._projections.student(self._user_id)
        )
        return {
            "section": self._describe(section),
            "canEnroll": verdict.can_enroll,
            "blockers": [b.value for b in verdict.blockers],
            "missingPrerequisites": list(verdict.missing_prerequisites),
            "conflictsWith": list(verdict.conflicts_with),
        }

    def propose_enrollment(self, args: dict[str, Any]) -> str:
        section = self._section(args)
        if section is None:
            return "error: la sección no existe en el periodo actual"
        reason = str(args.get("reason") or "").strip()
        if not reason:
            return "error: indica en una frase por qué propones esta sección"
        verdict = assess(
            section, self._projections.subject(section.subject_code), self._projections.student(self._user_id)
        )
        if not verdict.can_enroll:
            return "no se creó el plan: la matrícula lo rechazaría por " + ", ".join(b.value for b in verdict.blockers)
        self.proposal = Proposal(section, reason)
        return (
            "Plan creado y mostrado al estudiante para que lo confirme o lo descarte. No afirmes que la matrícula "
            "está hecha: queda pendiente de su confirmación y vence en 10 minutos."
        )

    # --- helpers
    def _section(self, args: dict[str, Any]) -> SectionView | None:
        try:
            section = self._projections.section(UUID(str(args.get("section_id", ""))))
        except ValueError:
            return None
        return section if section is not None and section.term == self._term else None

    @staticmethod
    def _describe(section: SectionView) -> dict[str, Any]:
        return {
            "sectionId": str(section.section_id),
            "subjectCode": section.subject_code,
            "subjectName": section.subject_name,
            "groupNumber": section.group_number,
            "professorName": section.professor_name,
            "slots": slots_of(section.slots),
            "seatsAvailable": section.seats_available,
        }
