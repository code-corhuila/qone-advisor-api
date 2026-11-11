"""Only what the advisor needs leaves Qampus (qone-docs#55, threat I on TB6): emails, Colombian
document numbers and phone numbers in what the student types become `[redacted]` before the
request reaches the agent, while `advisor.message` keeps what the student wrote."""

import pytest

from advisor.application.usecase.redaction import REDACTED, redact
from tests.fakes import ScriptedAgent
from tests.test_advisor_service import LAURA_CALLER, make

SENSITIVE = {
    "email": ("escríbeme a laura.gomez+qampus@corhuila.edu.co", "escríbeme a [redacted]"),
    "10-digit document": ("mi cédula es 1075234567", "mi cédula es [redacted]"),
    "6-digit document": ("TI 123456", "TI [redacted]"),
    "dotted document": ("CC 1.075.234.567 por favor", "CC [redacted] por favor"),
    "mobile": ("llámame al 3001234567", "llámame al [redacted]"),
    "spaced mobile": ("llámame al 300 123 4567.", "llámame al [redacted]."),
    "+57 mobile": ("whatsapp +57 300 123 4567", "whatsapp [redacted]"),
    "+57 joined": ("whatsapp +573001234567", "whatsapp [redacted]"),
    "+57 landline": ("fijo +57 608 875 3000", "fijo [redacted]"),
}

KEPT = [
    "Matricúlame ISW-602 grupo 1",
    "¿Qué me falta para graduarme en 2026?",
    "el periodo 2026-B, de 08:00 a 10:00",
    "llevo 38 % y 16 créditos, semestre 6",
    "la sección 55555555-5555-4555-8555-555555555510",
    "ISW-604 y RED-501 con 3 créditos",
    "pagué $3.500.000 de matrícula",
    "",
]


@pytest.mark.parametrize(("text", "expected"), SENSITIVE.values(), ids=SENSITIVE.keys())
def test_identifiers_become_redacted(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize("text", KEPT)
def test_codes_years_times_and_amounts_are_kept(text):
    assert redact(text) == text


def test_several_identifiers_in_one_message():
    text = "Soy Laura, CC 1075234567, correo laura@example.com, cel 3109876543. ¿Puedo ver ISW-602?"
    assert redact(text) == f"Soy Laura, CC {REDACTED}, correo {REDACTED}, cel {REDACTED}. ¿Puedo ver ISW-602?"


def test_the_agent_gets_redacted_text_and_the_store_keeps_what_the_student_wrote():
    agent = ScriptedAgent()
    service, store, *_ = make(agent)
    conversation = service.start_conversation(LAURA_CALLER, "conv-laura-redaction").item
    first = "Mi correo es laura@example.com"
    second = "Mi documento es 1075234567 y mi celular +57 300 123 4567, ¿qué me falta?"
    service.send_message(LAURA_CALLER, conversation.id, first, "message-key-1")
    service.send_message(LAURA_CALLER, conversation.id, second, "message-key-2")

    sent = [m.content for m in agent.requests[-1].history]
    assert sent[-1] == "Mi documento es [redacted] y mi celular [redacted], ¿qué me falta?"
    assert sent[0] == "Mi correo es [redacted]"  # the history is redacted too
    assert not any("laura@example.com" in c or "1075234567" in c for r in agent.requests for c in _texts(r))

    stored = [m.content for m in store.messages if m.conversation_id == conversation.id]
    assert first in stored and second in stored


def _texts(request) -> list[str]:
    return [m.content for m in request.history]
