"""The operations of the advisor (AD-01..AD-08) over the ports.

Who may do what (07-api/authentication.md): any person (STUDENT, PROFESSOR, ADMIN) starts and
reads their own conversations; a conversation or a plan of someone else does not exist for the
caller (404, D-03). A reply is produced by the agent with the tools of the conversation's role,
closed over its owner (INV-ADV-002); without the agent the reply is the fixed degraded message
and still 201 (D-02). Confirming a plan starts the saga through the advisor's service token with
`origin: AGENT` and the confirmation id (INV-ADV-003, INV-ENR-005); the student's token never
reaches the workflow. The saga answers when it ends, so its outcome settles the plan at once;
the consumer applies the same outcome from the enrollment events, idempotently."""

from datetime import timedelta
from uuid import UUID

from advisor.application.errors import Forbidden, NotFound, ServiceUnavailable
from advisor.application.port.inbound.advisor import (
    MAX_LIMIT,
    AdvisorUseCases,
    ChatReply,
    Confirmation,
    Created,
    Health,
    Page,
)
from advisor.application.port.inbound.caller import Caller, Role
from advisor.application.port.outbound.ports import (
    AdvisorStore,
    Agent,
    AgentRequest,
    AgentUnavailable,
    Clock,
    IdGenerator,
    Projections,
    SagaStarter,
    SagaUnavailable,
)
from advisor.application.usecase.tools import AdvisorTools
from advisor.domain.model.conversation import Conversation, Message
from advisor.domain.model.errors import FieldError, InvalidStatusTransition, ValidationError
from advisor.domain.model.events import plan_event
from advisor.domain.model.plan import Plan, PlanStatus, PlanSummary

KEY_MIN, KEY_MAX = 8, 128
HISTORY_MESSAGES = 20
PEOPLE = (Role.STUDENT, Role.PROFESSOR, Role.ADMIN)
DEGRADED_REPLY = (
    "El asesor no está disponible en este momento. Puedes seguir consultando tu pensum, matricular y pagar "
    "desde el portal; intenta de nuevo en unos minutos."
)
SAGA_OUTCOME = {"COMPLETED": True, "COMPENSATED": False, "FAILED": False}


class AdvisorService(AdvisorUseCases):
    def __init__(
        self,
        store: AdvisorStore,
        projections: Projections,
        agent: Agent | None,
        saga: SagaStarter,
        clock: Clock,
        ids: IdGenerator,
        current_term: str,
    ) -> None:
        self._store = store
        self._projections = projections
        self._agent = agent
        self._saga = saga
        self._clock = clock
        self._ids = ids
        self._term = current_term

    def health(self) -> Health:
        return Health(self._agent is not None, self._agent.model if self._agent else None)

    # --- conversations and messages (AD-02..AD-05)
    def start_conversation(self, caller: Caller, idempotency_key: str) -> Created[Conversation]:
        user_id = _person(caller)
        _check_key(idempotency_key)
        existing = self._store.conversation_by_key(idempotency_key)
        if existing is not None:
            return Created(existing, False)
        model = (self._agent.model if self._agent else None) or "none"
        conversation = Conversation.start(self._ids.new_id(), user_id, caller.role.value, model, self._clock.now())
        stored, created = self._store.create_conversation(conversation, idempotency_key)
        return Created(stored, created)

    def my_conversations(self, caller: Caller, page: int, limit: int) -> Page[Conversation]:
        user_id = _person(caller)
        _check_page(page, limit)
        items, total = self._store.conversations_of(user_id, page, limit)
        return Page(items, page, limit, total)

    def messages(self, caller: Caller, conversation_id: UUID, page: int, limit: int) -> Page[Message]:
        conversation = self._own_conversation(caller, conversation_id)
        _check_page(page, limit)
        items, total = self._store.messages_of(conversation.id, page, limit)
        return Page(items, page, limit, total)

    def send_message(
        self, caller: Caller, conversation_id: UUID, content: object, idempotency_key: str
    ) -> Created[ChatReply]:
        conversation = self._own_conversation(caller, conversation_id)
        _check_key(idempotency_key)
        prior = self._store.reply_by_key(idempotency_key)
        if prior is not None:
            return Created(ChatReply(prior[0], prior[1], prior[0].content == DEGRADED_REPLY), False)
        asked_at = self._clock.now()
        question = Message.from_user(self._ids.new_id(), conversation.id, content, asked_at)
        tools = AdvisorTools(conversation.user_id, conversation.user_role, self._projections, self._term)
        history = self._store.history(conversation.id, HISTORY_MESSAGES) + [question]
        try:
            if self._agent is None:
                raise AgentUnavailable("no API key configured")
            text, degraded = (
                self._agent.reply(AgentRequest(conversation.user_role, self._term, history, tools.for_role())),
                False,
            )
        except AgentUnavailable:
            text, degraded = DEGRADED_REPLY, True
        answered_at = max(self._clock.now(), asked_at + timedelta(microseconds=1))
        answer = Message.from_assistant(self._ids.new_id(), conversation.id, text, answered_at)
        plan = None
        if not degraded and tools.proposal is not None:
            section = tools.proposal.section
            summary = PlanSummary(
                section.subject_code,
                section.subject_name,
                section.group_number,
                section.professor_name,
                section.slots,
                section.seats_available,
                tools.proposal.reason,
            )
            plan = Plan.propose(
                self._ids.new_id(), conversation.id, conversation.user_id, section.section_id, summary, answered_at
            )
        events = [e for e in [plan_event(plan)] if e] if plan else []
        (message, stored_plan), created = self._store.record_reply(question, answer, plan, idempotency_key, events)
        return Created(ChatReply(message, stored_plan, degraded or message.content == DEGRADED_REPLY), created)

    # --- plans (AD-06..AD-08)
    def plan(self, caller: Caller, plan_id: UUID) -> Plan:
        return self._expire_if_due(self._own_plan(caller, plan_id))

    def confirm(self, caller: Caller, plan_id: UUID, correlation_id: str = "") -> Confirmation:
        plan = self._own_plan(caller, plan_id)
        if not (plan.status is PlanStatus.EXECUTING and plan.saga_id is None):  # else: a retry after 503
            now = self._clock.now()
            if plan.is_past_expiry(now):
                self._expire_if_due(plan)
                raise InvalidStatusTransition("the plan expired: ask the advisor for a new one (INV-ADV-003)")
            confirmed = plan.confirm(self._ids.new_id(), now)
            if not self._store.save_plan(confirmed, PlanStatus.PROPOSED, plan_event(confirmed)):
                raise InvalidStatusTransition("the plan changed meanwhile; read it again")
            plan = confirmed
        assert plan.confirmation_id is not None
        try:
            result = self._saga.start(
                plan.student_id, plan.section_id, plan.confirmation_id, f"advisor-plan-{plan.id}", correlation_id
            )
        except SagaUnavailable:
            raise ServiceUnavailable("the enrollment saga did not answer; confirm again to retry") from None
        started = plan.started(result.saga_id, self._clock.now())
        outcome = SAGA_OUTCOME.get(result.status)
        final = started.settle(outcome, started.updated_at) if outcome is not None else started
        self._store.save_plan(final, PlanStatus.EXECUTING, None)
        return Confirmation(final, result.saga_id)

    def dismiss(self, caller: Caller, plan_id: UUID) -> Plan:
        plan = self._own_plan(caller, plan_id)
        dismissed = plan.dismiss(self._clock.now())
        if dismissed is None:
            return plan  # D-04: any other state is returned unchanged
        if self._store.save_plan(dismissed, PlanStatus.PROPOSED, plan_event(dismissed)):
            return dismissed
        return self._store.plan(plan_id) or plan

    # --- helpers
    def _own_conversation(self, caller: Caller, conversation_id: UUID) -> Conversation:
        user_id = _person(caller)
        conversation = self._store.conversation(conversation_id)
        if conversation is None or not conversation.owned_by(user_id):
            raise NotFound()
        return conversation

    def _own_plan(self, caller: Caller, plan_id: UUID) -> Plan:
        user_id = _person(caller)
        plan = self._store.plan(plan_id)
        if plan is None or plan.student_id != user_id:
            raise NotFound()
        return plan

    def _expire_if_due(self, plan: Plan) -> Plan:
        expired = plan.expire(self._clock.now())
        if expired is None:
            return plan
        if self._store.save_plan(expired, PlanStatus.PROPOSED, None):
            return expired
        return self._store.plan(plan.id) or plan


def _person(caller: Caller) -> UUID:
    if caller.role not in PEOPLE or caller.user_id is None:
        raise Forbidden("only a person talks to the advisor")
    return caller.user_id


def _check_key(idempotency_key: str) -> None:
    if not KEY_MIN <= len(idempotency_key or "") <= KEY_MAX:
        raise ValidationError([FieldError("Idempotency-Key", "header required, 8 to 128 characters")])


def _check_page(page: int, limit: int) -> None:
    details: list[FieldError] = []
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        details.append(FieldError("page", "must be 1 or greater"))
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        details.append(FieldError("limit", f"must be between 1 and {MAX_LIMIT}"))
    if details:
        raise ValidationError(details)
