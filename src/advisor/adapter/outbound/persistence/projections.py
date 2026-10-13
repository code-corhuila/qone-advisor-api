"""The projection store over PostgreSQL: the inbox row and every change of an event in one
transaction. A late event (older than the row's updated_at) changes nothing; a PAID tuition never
goes back to ISSUED; an outcome moves only an EXECUTING plan of that saga."""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from advisor.application.usecase.projections import Change


class PostgresProjectionStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def apply(self, event_id: UUID, changes: list[Change], occurred_at: datetime) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            fresh = conn.execute(
                "INSERT INTO advisor.inbox (event_id) VALUES (%s) ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
                (event_id,),
            ).fetchone()
            if fresh is None:
                return False
            for kind, data in changes:
                getattr(self, f"_{kind}")(conn, data, occurred_at)
        return True

    @staticmethod
    def _subject(conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        conn.execute(
            "INSERT INTO advisor.catalog_subject_view (subject_id, code, name, credits, semester, prerequisites, "
            "updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (subject_id) DO UPDATE SET "
            "code = EXCLUDED.code, name = EXCLUDED.name, credits = EXCLUDED.credits, semester = EXCLUDED.semester, "
            "prerequisites = EXCLUDED.prerequisites, updated_at = EXCLUDED.updated_at "
            "WHERE advisor.catalog_subject_view.updated_at <= EXCLUDED.updated_at",
            (d["subjectId"], d["code"], d["name"], d["credits"], d["semester"], d["prerequisites"], at),
        )

    @staticmethod
    def _section(conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        conn.execute(
            "INSERT INTO advisor.catalog_section_view (section_id, subject_code, subject_name, term, group_number, "
            "professor_name, slots, seats_available, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (section_id) DO UPDATE SET subject_code = EXCLUDED.subject_code, "
            "subject_name = EXCLUDED.subject_name, term = EXCLUDED.term, group_number = EXCLUDED.group_number, "
            "professor_name = EXCLUDED.professor_name, slots = EXCLUDED.slots, "
            "seats_available = EXCLUDED.seats_available, updated_at = EXCLUDED.updated_at "
            "WHERE advisor.catalog_section_view.updated_at <= EXCLUDED.updated_at",
            (
                d["sectionId"], d["subjectCode"], d["subjectName"], d["term"], d["groupNumber"],
                d["professorName"], Jsonb(d["slots"]), d["seatsAvailable"], at,
            ),
        )  # fmt: skip

    @staticmethod
    def _seats(conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        conn.execute(
            "UPDATE advisor.catalog_section_view SET seats_available = %s, updated_at = %s "
            "WHERE section_id = %s AND updated_at <= %s",
            (d["seatsAvailable"], at, d["sectionId"], at),
        )

    @staticmethod
    def _tuition(conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        conn.execute(
            "INSERT INTO advisor.student_view (student_id, tuition_status, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (student_id) DO UPDATE SET tuition_status = EXCLUDED.tuition_status, "
            "updated_at = greatest(advisor.student_view.updated_at, EXCLUDED.updated_at) "
            "WHERE advisor.student_view.tuition_status IS DISTINCT FROM 'PAID'",
            (d["studentId"], d["status"], at),
        )

    @staticmethod
    def _confirmed_of(conn: psycopg.Connection, student_id: str, at: datetime) -> list[dict[str, Any]]:
        conn.execute(
            "INSERT INTO advisor.student_view (student_id, updated_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (student_id, at),
        )
        row = conn.execute(
            "SELECT confirmed_sections FROM advisor.student_view WHERE student_id = %s FOR UPDATE", (student_id,)
        ).fetchone()
        return list(row["confirmed_sections"])

    @classmethod
    def _enrolled(cls, conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        sections = [s for s in cls._confirmed_of(conn, d["studentId"], at) if s["sectionId"] != d["sectionId"]]
        view = conn.execute(
            "SELECT slots FROM advisor.catalog_section_view WHERE section_id = %s", (d["sectionId"],)
        ).fetchone()
        sections.append(
            {"sectionId": d["sectionId"], "subjectCode": d["subjectCode"], "slots": view["slots"] if view else []}
        )
        cls._write_sections(conn, d["studentId"], sections, at)

    @classmethod
    def _cancelled(cls, conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        sections = [s for s in cls._confirmed_of(conn, d["studentId"], at) if s["sectionId"] != d["sectionId"]]
        cls._write_sections(conn, d["studentId"], sections, at)

    @staticmethod
    def _write_sections(conn: psycopg.Connection, student_id: str, sections: list, at: datetime) -> None:
        conn.execute(
            "UPDATE advisor.student_view SET confirmed_sections = %s, updated_at = greatest(updated_at, %s) "
            "WHERE student_id = %s",
            (Jsonb(sections), at, student_id),
        )

    @staticmethod
    def _outcome(conn: psycopg.Connection, d: dict[str, Any], at: datetime) -> None:
        conn.execute(
            "UPDATE advisor.plan SET status = %s, updated_at = greatest(updated_at, %s) "
            "WHERE saga_id = %s AND status = 'EXECUTING'",
            (d["status"], at, d["sagaId"]),
        )
