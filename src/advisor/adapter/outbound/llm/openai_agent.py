"""The agent over LangChain and OpenAI (ADR-003). It binds the tools the use case hands it, lets
the model call them and stops when the model answers. One reply has a 120 s budget across all
its rounds; any provider error, a spent budget or an open circuit raises AgentUnavailable and the
use case degrades the reply (D-02). Identical prompts are served from the cache (llm_cache).

The system prompt goes first and does not change between turns, so the provider's own prompt
caching reuses it; the history and the question go last."""

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from langchain_core.caches import BaseCache
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from advisor.adapter.outbound.llm.breaker import CircuitBreaker
from advisor.application.port.outbound.ports import AgentRequest, AgentUnavailable, Tool
from advisor.domain.model.conversation import MessageRole

log = logging.getLogger("advisor.llm")
BUDGET_SECONDS = 120.0
MAX_TOOL_ROUNDS = 6
GAVE_UP = "No pude completar la consulta con las herramientas disponibles. Intenta reformular la pregunta."

SYSTEM_PROMPT = """Eres el Asesor Académico de Qampus, la plataforma de planeación y matrícula de la \
Corporación Universitaria del Huila (CORHUILA). Periodo académico actual: {term}. Hablas con un usuario \
con rol {role}.

Reglas, en orden de prioridad:
1. Responde SOLO con datos obtenidos de tus herramientas. Si no tienes el dato, dilo; nunca lo inventes. \
Cada materia que menciones debe aparecer en el pensum que devuelve degree_audit.
2. Tus herramientas solo leen los datos del usuario con quien hablas. Si te piden datos de otra persona, \
explica que no puedes acceder a ellos.
3. No puedes ejecutar ninguna matrícula. Tu única acción es proponer un plan con propose_enrollment; el \
estudiante lo confirma o lo descarta en la interfaz y el plan vence a los 10 minutos.
4. Antes de proponer, verifica la sección con check_section (pago, materia ya inscrita, prerrequisitos, \
cruce de horario y cupos). Nunca calcules cruces tú: dos clases contiguas no se cruzan. Si hay bloqueos, \
explícalos y ofrece alternativas de list_sections.
5. No puedes pagar facturas, editar el catálogo ni actuar sobre otros usuarios; si te lo piden, explica que \
eso se hace desde el portal.
6. Responde en español, breve y concreto, en texto plano (sin Markdown); para listas usa guiones simples."""


def as_openai_tool(tool: Tool) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
    }


class OpenAIAgent:
    def __init__(
        self,
        model: str,
        api_key: str,
        breaker: CircuitBreaker | None = None,
        cache: BaseCache | None = None,
        budget_seconds: float = BUDGET_SECONDS,
        chat_factory: Callable[[float], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = model
        self._breaker = breaker or CircuitBreaker()
        self._budget = budget_seconds
        self._clock = clock
        self._chat_factory = chat_factory or (
            lambda timeout: ChatOpenAI(
                model=model, api_key=api_key, temperature=0, request_timeout=timeout, max_retries=1, cache=cache
            )
        )

    @property
    def model(self) -> str:
        return self._model

    def reply(self, request: AgentRequest) -> str:
        if not self._breaker.allow():
            raise AgentUnavailable("circuit open")
        try:
            text = self._converse(request)
        except Exception as error:  # noqa: BLE001 - any provider failure degrades the reply (D-02)
            self._breaker.record_failure()
            log.warning("agent unavailable", extra={"detail": f"{type(error).__name__}: {error}"})
            raise AgentUnavailable(str(error)) from error
        self._breaker.record_success()
        return text

    def _converse(self, request: AgentRequest) -> str:
        deadline = self._clock() + self._budget
        chat = self._chat_factory(self._budget)
        model = chat.bind_tools([as_openai_tool(t) for t in request.tools]) if request.tools else chat
        by_name = {t.name: t for t in request.tools}
        messages: list[BaseMessage] = [SystemMessage(SYSTEM_PROMPT.format(term=request.term, role=request.role.value))]
        for m in request.history:
            messages.append(HumanMessage(m.content) if m.role is MessageRole.USER else AIMessage(m.content))
        for _ in range(MAX_TOOL_ROUNDS):
            if self._clock() >= deadline:
                raise TimeoutError(f"the {self._budget:.0f} s budget of the reply was spent")
            answer = model.invoke(messages)
            messages.append(answer)
            if not answer.tool_calls:
                return str(answer.content)
            for call in answer.tool_calls:
                messages.append(
                    ToolMessage(self._run_tool(by_name.get(call["name"]), call["args"]), tool_call_id=call["id"])
                )
        return GAVE_UP

    @staticmethod
    def _run_tool(tool: Tool | None, args: dict[str, Any]) -> str:
        if tool is None:
            return "herramienta no disponible para este usuario"
        try:
            result = tool.run(args or {})
        except Exception as error:  # noqa: BLE001 - the model is told, the reply goes on
            log.warning("tool failed", extra={"detail": f"{tool.name}: {error}"})
            return f"error al consultar {tool.name}"
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
