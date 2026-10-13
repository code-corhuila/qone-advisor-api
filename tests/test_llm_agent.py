"""The LLM adapter with a scripted chat model: the tool loop, the 120 s budget, the circuit
breaker (3 failures, 60 s) and the degraded path when the provider fails. No network."""

from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from advisor.adapter.outbound.llm.breaker import CircuitBreaker
from advisor.adapter.outbound.llm.openai_agent import GAVE_UP, MAX_TOOL_ROUNDS, OpenAIAgent
from advisor.application.port.outbound.ports import AgentRequest, AgentUnavailable, Tool
from advisor.domain.model.conversation import Message, UserRole
from tests.fakes import MovableClock

ASKED = Message.from_user(UUID(int=1), UUID(int=2), "¿Qué me falta?", MovableClock().now())
AUDIT = Tool("degree_audit", "pensum", {"type": "object", "properties": {}}, lambda _: {"percent": 38})
BROKEN = Tool("tuition_status", "pago", {"type": "object", "properties": {}}, lambda _: 1 / 0)


class ScriptedChat:
    def __init__(self, answers, clock=None, step=0.0):
        self.answers = list(answers)
        self.seen: list[list] = []
        self.bound: list = []
        self.clock, self.step = clock, step

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def invoke(self, messages):
        self.seen.append(list(messages))
        if self.clock:
            self.clock[0] += self.step
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def call(name: str, n: int = 1) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": f"call-{n}"}])


def agent(chat: ScriptedChat, breaker=None, clock=None) -> OpenAIAgent:
    ticks = clock or [0.0]
    return OpenAIAgent(
        "gpt-4o-mini", "sk-test", breaker or CircuitBreaker(), chat_factory=lambda _: chat, clock=lambda: ticks[0]
    )


def request(*tools: Tool) -> AgentRequest:
    return AgentRequest(UserRole.STUDENT, "2026-B", [ASKED], list(tools))


def test_the_model_calls_a_tool_and_answers_with_its_result():
    chat = ScriptedChat([call("degree_audit"), AIMessage(content="Llevas 38 %.")])
    assert agent(chat).reply(request(AUDIT)) == "Llevas 38 %."
    assert chat.bound[0]["function"]["name"] == "degree_audit"
    first, second = chat.seen
    assert isinstance(first[0], SystemMessage) and "2026-B" in first[0].content and "STUDENT" in first[0].content
    assert first[-1].content == "¿Qué me falta?"
    assert isinstance(second[-1], ToolMessage) and second[-1].content == '{"percent": 38}'


def test_a_failing_or_unknown_tool_is_reported_to_the_model_not_to_the_user():
    chat = ScriptedChat([call("tuition_status"), call("propose_enrollment", 2), AIMessage(content="ok")])
    assert agent(chat).reply(request(BROKEN)) == "ok"
    assert chat.seen[1][-1].content == "error al consultar tuition_status"
    assert chat.seen[2][-1].content == "herramienta no disponible para este usuario"


def test_the_loop_stops_after_the_last_round():
    chat = ScriptedChat([call("degree_audit", n) for n in range(MAX_TOOL_ROUNDS)])
    assert agent(chat).reply(request(AUDIT)) == GAVE_UP


def test_the_budget_of_a_reply_is_120_seconds_across_rounds():
    clock = [0.0]
    chat = ScriptedChat([call("degree_audit"), AIMessage(content="tarde")], clock, step=121.0)
    with pytest.raises(AgentUnavailable, match="budget"):
        agent(chat, clock=clock).reply(request(AUDIT))


def test_three_provider_failures_open_the_circuit_for_sixty_seconds():
    now = [0.0]
    breaker = CircuitBreaker(clock=lambda: now[0])
    chat = ScriptedChat([RuntimeError("503 from provider")] * 3 + [AIMessage(content="volví")])
    advisor = agent(chat, breaker)
    for _ in range(3):
        with pytest.raises(AgentUnavailable):
            advisor.reply(request())
    assert breaker.state == "open"
    with pytest.raises(AgentUnavailable, match="circuit open"):
        advisor.reply(request())
    assert len(chat.seen) == 3  # the open circuit did not call the provider
    now[0] = 60.0
    assert breaker.state == "half-open"
    assert advisor.reply(request()) == "volví" and breaker.state == "closed"


def test_a_failed_trial_call_reopens_the_circuit():
    now = [0.0]
    breaker = CircuitBreaker(clock=lambda: now[0])
    for _ in range(3):
        breaker.record_failure()
    now[0] = 61.0
    assert breaker.allow() and not breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open"
