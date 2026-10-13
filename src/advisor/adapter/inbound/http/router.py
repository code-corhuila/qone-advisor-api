"""The routes of AD-01..AD-08 (07-api/contracts/openapi/qone-advisor-api.yaml) and their
representations. Domain entities are never serialized directly (Annex C)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from advisor.application.port.inbound.advisor import DEFAULT_LIMIT, AdvisorUseCases, ChatReply
from advisor.application.port.inbound.caller import Caller
from advisor.application.usecase.advisor import KEY_MAX, KEY_MIN
from advisor.domain.model.conversation import Conversation, Message
from advisor.domain.model.errors import FieldError, ValidationError
from advisor.domain.model.plan import Plan

PREFIX = "/api/v1/advisor"
router = APIRouter(prefix=PREFIX)


def use_cases(request: Request) -> AdvisorUseCases:
    return request.app.state.use_cases


def caller(request: Request) -> Caller:
    return request.app.state.verifier.verify(request.headers.get("Authorization"))


UseCases = Annotated[AdvisorUseCases, Depends(use_cases)]
Current = Annotated[Caller, Depends(caller)]


class MessageBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: StrictStr


@router.get("/health")
def health(request: Request, service: UseCases):
    status = service.health()
    return {
        "status": "ok",
        "service": request.app.state.service,
        "version": request.app.state.version,
        "llm": "configured" if status.llm_configured else "missing",
        "model": status.model,
    }


@router.post("/conversations")
def start_conversation(who: Current, service: UseCases, request: Request):
    result = service.start_conversation(who, idempotency_key_of(request))
    location = f"{PREFIX}/conversations/{result.item.id}/messages"
    return created(represent_conversation(result.item), result.created, location)


@router.get("/conversations/me")
def my_conversations(who: Current, service: UseCases, page: int = Query(1), limit: int = Query(DEFAULT_LIMIT)):
    return page_of(service.my_conversations(who, page, limit), represent_conversation)


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: str, who: Current, service: UseCases, page: int = Query(1), limit: int = Query(DEFAULT_LIMIT)
):
    return page_of(service.messages(who, parse_uuid(conversation_id, "id"), page, limit), represent_message)


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: str, body: MessageBody, who: Current, service: UseCases, request: Request):
    parsed = parse_uuid(conversation_id, "id")
    result = service.send_message(who, parsed, body.content, idempotency_key_of(request))
    return JSONResponse(represent_reply(result.item), status_code=201 if result.created else 200)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, who: Current, service: UseCases):
    return represent_plan(service.plan(who, parse_uuid(plan_id, "id")))


@router.post("/plans/{plan_id}/confirm")
def confirm(plan_id: str, who: Current, service: UseCases, request: Request):
    result = service.confirm(who, parse_uuid(plan_id, "id"), getattr(request.state, "correlation_id", ""))
    return JSONResponse({"plan": represent_plan(result.plan), "sagaId": str(result.saga_id)}, status_code=202)


@router.post("/plans/{plan_id}/dismiss")
def dismiss(plan_id: str, who: Current, service: UseCases):
    return represent_plan(service.dismiss(who, parse_uuid(plan_id, "id")))


# --- border helpers
def idempotency_key_of(request: Request) -> str:
    key = request.headers.get("Idempotency-Key", "")
    if not KEY_MIN <= len(key) <= KEY_MAX:
        raise ValidationError([FieldError("Idempotency-Key", "header required, 8 to 128 characters")])
    return key


def parse_uuid(value: str | None, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError([FieldError(field, "must be a UUID")]) from None


def created(payload: dict, is_new: bool, location: str) -> JSONResponse:
    if not is_new:
        return JSONResponse(payload, status_code=200)
    return JSONResponse(payload, status_code=201, headers={"Location": location})


def page_of(page, represent) -> dict:
    return {
        "data": [represent(i) for i in page.items],
        "meta": {"page": page.page, "limit": page.limit, "total": page.total, "totalPages": page.total_pages},
    }


def rfc3339(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- representations
def represent_conversation(c: Conversation) -> dict:
    return {
        "id": str(c.id),
        "userId": str(c.user_id),
        "userRole": c.user_role.value,
        "model": c.model,
        "createdAt": rfc3339(c.created_at),
    }


def represent_message(m: Message) -> dict:
    return {
        "id": str(m.id),
        "conversationId": str(m.conversation_id),
        "role": m.role.value,
        "content": m.content,
        "createdAt": rfc3339(m.created_at),
    }


def represent_plan(p: Plan) -> dict:
    s = p.summary
    return {
        "id": str(p.id),
        "conversationId": str(p.conversation_id),
        "studentId": str(p.student_id),
        "sectionId": str(p.section_id),
        "summary": {
            "subjectCode": s.subject_code,
            "subjectName": s.subject_name,
            "groupNumber": s.group_number,
            "professorName": s.professor_name,
            "slots": [
                {"day": x.day.value, "start": x.start.strftime("%H:%M"), "end": x.end.strftime("%H:%M")}
                for x in sorted(s.slots)
            ],
            "seatsAvailable": s.seats_available,
            "reason": s.reason,
        },
        "status": p.status.value,
        "confirmationId": str(p.confirmation_id) if p.confirmation_id else None,
        "sagaId": str(p.saga_id) if p.saga_id else None,
        "expiresAt": rfc3339(p.expires_at),
        "createdAt": rfc3339(p.created_at),
    }


def represent_reply(reply: ChatReply) -> dict:
    return {
        "message": represent_message(reply.message),
        "plan": represent_plan(reply.plan) if reply.plan else None,
        "degraded": reply.degraded,
    }
