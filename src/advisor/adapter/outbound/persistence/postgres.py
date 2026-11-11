"""The advisor's store and projections over PostgreSQL (schema `advisor` of qone-advisor-db) with
psycopg 3 and a pool. A creation claims its Idempotency-Key before writing anything and returns
the first result when the key was used (norm 5.3.8); a reply writes the key, both messages, the
plan and its events in ONE transaction; a plan is updated only from the state it was read in.
Messages are only inserted (INV-ADV-004)."""

import threading
from concurrent.futures import Future
from datetime import UTC, datetime, time
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from advisor.application.errors import IdempotencyKeyReused
from advisor.application.port.outbound.ports import RequestKey
from advisor.domain.model.advising import ConfirmedSection, SectionView, StudentView, SubjectView
from advisor.domain.model.conversation import Conversation, Message, MessageRole, UserRole
from advisor.domain.model.events import DomainEvent
from advisor.domain.model.plan import Plan, PlanStatus, PlanSummary
from advisor.domain.model.slot import Day, Slot

PLAN_COLUMNS = (
    "id, conversation_id, student_id, section_id, summary, status, confirmation_id, saga_id, expires_at, created_at, "
    "updated_at"
)


def connect(
    url: str, max_size: int = 10, wait_seconds: float = 5.0, statement_timeout_ms: int = 5000
) -> ConnectionPool:
    return ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=max_size,
        timeout=wait_seconds,
        kwargs={"options": f"-c statement_timeout={statement_timeout_ms}", "row_factory": dict_row},
        open=True,
    )


class PostgresReachable:
    """The ready probe's check of the database (HU-PLT-004): `SELECT 1` on a pooled connection
    within a deadline of `timeout_seconds`. The pool bounds the wait for a connection, but a query
    on a pooled connection whose engine stopped answering (paused, partitioned) would block until
    TCP gives up; so the check runs on a daemon thread and the probe waits for it at most the
    timeout. The next probes share a check still pending instead of starting another one: at most
    one thread is ever stuck."""

    name = "database"

    def __init__(self, pool: ConnectionPool, timeout_seconds: float = 1.0) -> None:
        self._pool = pool
        self._timeout = timeout_seconds
        self._lock = threading.Lock()
        self._pending: Future | None = None

    def reachable(self) -> bool:
        with self._lock:
            if self._pending is None or self._pending.done():
                self._pending = Future()
                check = threading.Thread(target=self._select_one, args=(self._pending,), name="probe", daemon=True)
                check.start()
            pending = self._pending
        try:
            return pending.result(timeout=self._timeout)
        except (TimeoutError, psycopg.Error):
            return False

    def _select_one(self, outcome: Future) -> None:
        try:
            with self._pool.connection(timeout=self._timeout) as conn:
                conn.execute("SELECT 1")
        except Exception as error:  # noqa: BLE001 - handed to the waiting probe as is
            outcome.set_exception(error)
        else:
            outcome.set_result(True)


class _KeyAlreadyUsed(Exception):
    pass


def _claim_key(conn, key: RequestKey, resource_type: str, resource_id: UUID) -> None:
    taken = conn.execute(
        "INSERT INTO advisor.idempotency_key (owner_id, key, resource_type, resource_id, request_hash) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (owner_id, key) DO NOTHING RETURNING key",
        (key.owner_id, key.value, resource_type, resource_id, key.request_hash),
    ).fetchone()
    if taken is None:
        raise _KeyAlreadyUsed()


def _resource_of(conn, key: RequestKey, resource_type: str) -> UUID | None:
    """What the owner created with this key; a key of another request raises (#55). Keys written
    before V014 of qone-advisor-db have no hash and keep answering their replay."""
    row = conn.execute(
        "SELECT resource_type, resource_id, request_hash FROM advisor.idempotency_key WHERE owner_id = %s AND key = %s",
        (key.owner_id, key.value),
    ).fetchone()
    if row is None:
        return None
    if row["resource_type"] != resource_type or row["request_hash"] not in (None, key.request_hash):
        raise IdempotencyKeyReused()
    return row["resource_id"]


def _write_event(conn, event: DomainEvent) -> None:
    conn.execute(
        "INSERT INTO advisor.outbox (aggregate_type, aggregate_id, event_type, payload, occurred_at) VALUES (%s, %s, "
        "%s, %s, %s)",
        (event.aggregate_type, event.aggregate_id, event.event_type, Jsonb(event.payload), event.occurred_at),
    )


def _utc(moment):
    return moment.astimezone(UTC)


def slots_from(items) -> tuple[Slot, ...]:
    return tuple(Slot(Day(s["day"]), time.fromisoformat(s["start"]), time.fromisoformat(s["end"])) for s in items or [])


def slots_to(slots: tuple[Slot, ...]) -> list[dict[str, str]]:
    return [{"day": s.day.value, "start": s.start.strftime("%H:%M"), "end": s.end.strftime("%H:%M")} for s in slots]


def _conversation(row) -> Conversation:
    return Conversation(row["id"], row["user_id"], UserRole(row["user_role"]), row["model"], _utc(row["created_at"]))


def _message(row) -> Message:
    return Message(row["id"], row["conversation_id"], MessageRole(row["role"]), row["content"], _utc(row["created_at"]))


def _plan(row) -> Plan:
    s = row["summary"]
    summary = PlanSummary(
        s["subjectCode"],
        s["subjectName"],
        s["groupNumber"],
        s["professorName"],
        slots_from(s["slots"]),
        s["seatsAvailable"],
        s["reason"],
    )
    return Plan(
        row["id"], row["conversation_id"], row["student_id"], row["section_id"], summary, PlanStatus(row["status"]),
        row["confirmation_id"], row["saga_id"], _utc(row["expires_at"]), _utc(row["created_at"]),
        _utc(row["updated_at"]),
    )  # fmt: skip


def _summary_json(s: PlanSummary) -> Jsonb:
    return Jsonb(
        {
            "subjectCode": s.subject_code,
            "subjectName": s.subject_name,
            "groupNumber": s.group_number,
            "professorName": s.professor_name,
            "slots": slots_to(s.slots),
            "seatsAvailable": s.seats_available,
            "reason": s.reason,
        }
    )


class PostgresAdvisorStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def conversation_by_key(self, key: RequestKey) -> Conversation | None:
        with self._pool.connection() as conn:
            found = _resource_of(conn, key, "conversation")
        return self.conversation(found) if found else None

    def create_conversation(self, conversation: Conversation, key: RequestKey) -> tuple[Conversation, bool]:
        c = conversation
        try:
            with self._pool.connection() as conn, conn.transaction():
                _claim_key(conn, key, "conversation", c.id)
                conn.execute(
                    "INSERT INTO advisor.conversation (id, user_id, user_role, model, created_at) VALUES (%s, %s, %s, "
                    "%s, %s)",
                    (c.id, c.user_id, c.user_role.value, c.model, c.created_at),
                )
        except _KeyAlreadyUsed:
            earlier = self.conversation_by_key(key)
            assert earlier is not None
            return earlier, False
        return c, True

    def conversation(self, conversation_id: UUID) -> Conversation | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM advisor.conversation WHERE id = %s", (conversation_id,)).fetchone()
        return _conversation(row) if row else None

    def conversations_of(self, user_id: UUID, page: int, limit: int) -> tuple[list[Conversation], int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT *, count(*) OVER () AS total FROM advisor.conversation WHERE user_id = %s "
                "ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                (user_id, limit, (page - 1) * limit),
            ).fetchall()
            total = rows[0]["total"] if rows else self._count(conn, "conversation", "user_id", user_id)
        return [_conversation(r) for r in rows], total

    def messages_of(self, conversation_id: UUID, page: int, limit: int) -> tuple[list[Message], int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT *, count(*) OVER () AS total FROM advisor.message WHERE conversation_id = %s "
                "ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
                (conversation_id, limit, (page - 1) * limit),
            ).fetchall()
            total = rows[0]["total"] if rows else self._count(conn, "message", "conversation_id", conversation_id)
        return [_message(r) for r in rows], total

    def history(self, conversation_id: UUID, last: int) -> list[Message]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM advisor.message WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s",
                (conversation_id, last),
            ).fetchall()
        return [_message(r) for r in reversed(rows)]

    def reply_by_key(self, key: RequestKey) -> tuple[Message, Plan | None] | None:
        with self._pool.connection() as conn:
            answer_id = _resource_of(conn, key, "reply")
            if answer_id is None:
                return None
            answer = conn.execute("SELECT * FROM advisor.message WHERE id = %s", (answer_id,)).fetchone()
            plan = conn.execute(
                f"SELECT {PLAN_COLUMNS} FROM advisor.plan WHERE conversation_id = %s AND created_at = %s",
                (answer["conversation_id"], answer["created_at"]),
            ).fetchone()
        return _message(answer), _plan(plan) if plan else None

    def user_messages_since(self, user_id: UUID, since: datetime) -> tuple[int, datetime | None]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n, min(m.created_at) AS oldest FROM advisor.message m "
                "JOIN advisor.conversation c ON c.id = m.conversation_id "
                "WHERE c.user_id = %s AND m.role = 'USER' AND m.created_at > %s",
                (user_id, since),
            ).fetchone()
        return row["n"], _utc(row["oldest"]) if row["oldest"] else None

    def record_reply(
        self, question: Message, answer: Message, plan: Plan | None, key: RequestKey, events: list[DomainEvent]
    ) -> tuple[tuple[Message, Plan | None], bool]:
        try:
            with self._pool.connection() as conn, conn.transaction():
                _claim_key(conn, key, "reply", answer.id)
                for m in (question, answer):
                    conn.execute(
                        "INSERT INTO advisor.message (id, conversation_id, role, content, created_at) VALUES (%s, %s, "
                        "%s, %s, %s)",
                        (m.id, m.conversation_id, m.role.value, m.content, m.created_at),
                    )
                if plan is not None:
                    conn.execute(
                        f"INSERT INTO advisor.plan ({PLAN_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s)",
                        (plan.id, plan.conversation_id, plan.student_id, plan.section_id, _summary_json(plan.summary),
                         plan.status.value, plan.confirmation_id, plan.saga_id, plan.expires_at, plan.created_at,
                         plan.updated_at),
                    )  # fmt: skip
                for event in events:
                    _write_event(conn, event)
        except _KeyAlreadyUsed:
            earlier = self.reply_by_key(key)
            assert earlier is not None
            return earlier, False
        return (answer, plan), True

    def plan(self, plan_id: UUID) -> Plan | None:
        with self._pool.connection() as conn:
            row = conn.execute(f"SELECT {PLAN_COLUMNS} FROM advisor.plan WHERE id = %s", (plan_id,)).fetchone()
        return _plan(row) if row else None

    def save_plan(self, plan: Plan, expected: PlanStatus, event: DomainEvent | None) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            moved = conn.execute(
                "UPDATE advisor.plan SET status = %s, confirmation_id = %s, saga_id = %s, updated_at = %s "
                "WHERE id = %s AND status = %s RETURNING id",
                (plan.status.value, plan.confirmation_id, plan.saga_id, plan.updated_at, plan.id, expected.value),
            ).fetchone()
            if moved is None:
                return False
            if event is not None:
                _write_event(conn, event)
        return True

    @staticmethod
    def _count(conn, table: str, column: str, value: UUID) -> int:
        return conn.execute(f"SELECT count(*) AS n FROM advisor.{table} WHERE {column} = %s", (value,)).fetchone()["n"]


class PostgresProjections:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def pensum(self) -> list[SubjectView]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM advisor.catalog_subject_view ORDER BY semester, code").fetchall()
        return [SubjectView(r["code"], r["name"], r["credits"], r["semester"], tuple(r["prerequisites"])) for r in rows]

    def subject(self, code: str) -> SubjectView | None:
        return next((s for s in self.pensum() if s.code == code), None)

    def sections(self, term: str, query: str | None = None) -> list[SectionView]:
        like = f"%{query.strip()}%" if query and query.strip() else "%"
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM advisor.catalog_section_view WHERE term = %s AND (subject_code ILIKE %s OR "
                "subject_name ILIKE %s) "
                "ORDER BY subject_code, group_number",
                (term, like, like),
            ).fetchall()
        return [self._section(r) for r in rows]

    def section(self, section_id: UUID) -> SectionView | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM advisor.catalog_section_view WHERE section_id = %s", (section_id,)
            ).fetchone()
        return self._section(row) if row else None

    def student(self, student_id: UUID) -> StudentView:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM advisor.student_view WHERE student_id = %s", (student_id,)).fetchone()
        if row is None:
            return StudentView(student_id)
        confirmed = tuple(
            ConfirmedSection(UUID(c["sectionId"]), c["subjectCode"], slots_from(c.get("slots")))
            for c in row["confirmed_sections"]
        )
        return StudentView(student_id, frozenset(row["passed_codes"]), confirmed, row["tuition_status"])

    @staticmethod
    def _section(r) -> SectionView:
        return SectionView(
            r["section_id"], r["subject_code"], r["subject_name"], r["term"], r["group_number"], r["professor_name"],
            slots_from(r["slots"]), r["seats_available"],
        )  # fmt: skip
