import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field

load_dotenv()

APP_NAME = "agentmail-review-service"
AGENTMAIL_SEND_URL = os.getenv("AGENTMAIL_SEND_URL", "http://localhost:9000/send")
SEND_TIMEOUT_SECONDS = float(os.getenv("SEND_TIMEOUT_SECONDS", "20"))

app = FastAPI(title=APP_NAME, version="0.2.0")


class ReviewTaskIn(BaseModel):
    action: Literal["queue_for_human_review"] = "queue_for_human_review"
    reason: str = "low_confidence_or_high_risk"
    provider_msg_id: str
    from_addr: EmailStr
    subject: str = ""
    draft: str
    confidence: float = 0.0
    citations: List[Dict[str, Any]] = Field(default_factory=list)


class ReviewTask(ReviewTaskIn):
    status: Literal["pending", "approved", "rejected"] = "pending"
    reviewer: Optional[str] = None
    final_reply: Optional[str] = None
    reject_reason: Optional[str] = None
    created_at: str
    updated_at: str


class ApproveRequest(BaseModel):
    reviewer: str
    final_reply: Optional[str] = None


class RejectRequest(BaseModel):
    reviewer: str
    reason: Optional[str] = None


TASKS: Dict[str, ReviewTask] = {}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _send_email(to_addr: str, subject: str, body_text: str, provider_msg_id: str) -> Dict[str, Any]:
    payload = {
        "action": "auto_send",
        "provider_msg_id": provider_msg_id,
        "to": to_addr,
        "subject": f"Re: {subject}",
        "body_text": body_text,
    }
    with httpx.Client(timeout=SEND_TIMEOUT_SECONDS) as client:
        resp = client.post(AGENTMAIL_SEND_URL, json=payload)
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"ok": True}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


@app.post("/review/queue")
def queue_review(task: ReviewTaskIn) -> Dict[str, Any]:
    existing = TASKS.get(task.provider_msg_id)
    if existing and existing.status == "pending":
        return {"ok": True, "queued": False, "provider_msg_id": task.provider_msg_id}
    now = _now_iso()
    TASKS[task.provider_msg_id] = ReviewTask(**task.model_dump(), created_at=now, updated_at=now)
    return {"ok": True, "queued": True, "provider_msg_id": task.provider_msg_id}


@app.get("/review/tasks")
def list_tasks(status: Optional[str] = "pending") -> Dict[str, Any]:
    items = list(TASKS.values())
    if status:
        items = [t for t in items if t.status == status]
    return {"count": len(items), "items": items}


@app.get("/review/tasks/{provider_msg_id}")
def get_task(provider_msg_id: str) -> ReviewTask:
    task = TASKS.get(provider_msg_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.post("/review/tasks/{provider_msg_id}/approve")
def approve_task(provider_msg_id: str, payload: ApproveRequest) -> Dict[str, Any]:
    task = TASKS.get(provider_msg_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "pending":
        raise HTTPException(status_code=400, detail="task already processed")

    final_reply = payload.final_reply or task.draft
    send_result = _send_email(
        to_addr=task.from_addr,
        subject=task.subject,
        body_text=final_reply,
        provider_msg_id=provider_msg_id,
    )

    task.status = "approved"
    task.reviewer = payload.reviewer
    task.final_reply = final_reply
    task.updated_at = _now_iso()
    TASKS[provider_msg_id] = task
    return {"ok": True, "provider_msg_id": provider_msg_id, "send_result": send_result}


@app.post("/review/tasks/{provider_msg_id}/reject")
def reject_task(provider_msg_id: str, payload: RejectRequest) -> Dict[str, Any]:
    task = TASKS.get(provider_msg_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "pending":
        raise HTTPException(status_code=400, detail="task already processed")

    task.status = "rejected"
    task.reviewer = payload.reviewer
    task.reject_reason = payload.reason or ""
    task.updated_at = _now_iso()
    TASKS[provider_msg_id] = task
    return {"ok": True, "provider_msg_id": provider_msg_id, "reason": task.reject_reason}


@app.post("/mock/agentmail/send")
def mock_agentmail_send(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Local integration test endpoint used by docker-compose default env.
    return {"ok": True, "accepted": True, "echo": payload}
