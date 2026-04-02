import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

import httpx
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field

load_dotenv()

APP_NAME = "agentmail-review-service"
STORE_BACKEND = os.getenv("STORE_BACKEND", "postgres").lower()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://agentmail:agentmail@localhost:5432/agentmail")
AGENTMAIL_SEND_URL = os.getenv("AGENTMAIL_SEND_URL", "http://127.0.0.1:8100/mock/agentmail/send")
SEND_TIMEOUT_SECONDS = float(os.getenv("SEND_TIMEOUT_SECONDS", "20"))

app = FastAPI(title=APP_NAME, version="0.3.0")


class ReviewTaskIn(BaseModel):
    action: Literal["queue_for_human_review"] = "queue_for_human_review"
    reason: str = "low_confidence_or_high_risk"
    provider_msg_id: str
    from_addr: EmailStr
    subject: str = ""
    draft: str
    confidence: float = 0.0
    citations: List[Dict[str, Any]] = Field(default_factory=list)


class ReviewTask(BaseModel):
    action: Literal["queue_for_human_review"] = "queue_for_human_review"
    reason: str = "low_confidence_or_high_risk"
    provider_msg_id: str
    from_addr: EmailStr
    subject: str = ""
    draft: str
    confidence: float = 0.0
    citations: List[Dict[str, Any]] = Field(default_factory=list)
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


def _db_enabled() -> bool:
    return STORE_BACKEND == "postgres"


def _db_conn() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


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


def _row_to_task(row: Dict[str, Any]) -> ReviewTask:
    citations = row.get("citations") or []
    return ReviewTask(
        action="queue_for_human_review",
        reason=row.get("reason") or "low_confidence_or_high_risk",
        provider_msg_id=row["provider_msg_id"],
        from_addr=row["from_addr"],
        subject=row.get("subject") or "",
        draft=row["draft"],
        confidence=float(row.get("confidence") or 0.0),
        citations=citations,
        status=row["status"],
        reviewer=row.get("reviewer"),
        final_reply=row.get("final_reply"),
        reject_reason=row.get("reject_reason"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _fetch_task_db(provider_msg_id: str) -> Optional[ReviewTask]:
    with _db_conn() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT provider_msg_id, from_addr, subject, draft, confidence, citations,
                       reason, status, reviewer, final_reply, reject_reason, created_at, updated_at
                FROM review_tasks
                WHERE provider_msg_id = %s
                """,
                (provider_msg_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_task(row)


def _list_tasks_db(status: Optional[str]) -> List[ReviewTask]:
    with _db_conn() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            if status:
                cur.execute(
                    """
                    SELECT provider_msg_id, from_addr, subject, draft, confidence, citations,
                           reason, status, reviewer, final_reply, reject_reason, created_at, updated_at
                    FROM review_tasks
                    WHERE status = %s
                    ORDER BY created_at DESC
                    """,
                    (status,),
                )
            else:
                cur.execute(
                    """
                    SELECT provider_msg_id, from_addr, subject, draft, confidence, citations,
                           reason, status, reviewer, final_reply, reject_reason, created_at, updated_at
                    FROM review_tasks
                    ORDER BY created_at DESC
                    """
                )
            return [_row_to_task(r) for r in cur.fetchall()]


def _queue_task_db(task: ReviewTaskIn) -> bool:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO review_tasks (
                    provider_msg_id, from_addr, subject, draft, confidence, citations, reason, status
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 'pending')
                ON CONFLICT (provider_msg_id) DO UPDATE SET
                    from_addr = EXCLUDED.from_addr,
                    subject = EXCLUDED.subject,
                    draft = EXCLUDED.draft,
                    confidence = EXCLUDED.confidence,
                    citations = EXCLUDED.citations,
                    reason = EXCLUDED.reason,
                    status = CASE
                        WHEN review_tasks.status = 'pending' THEN review_tasks.status
                        ELSE 'pending'
                    END,
                    reviewer = NULL,
                    final_reply = NULL,
                    reject_reason = NULL,
                    updated_at = NOW()
                """,
                (
                    task.provider_msg_id,
                    str(task.from_addr),
                    task.subject,
                    task.draft,
                    task.confidence,
                    psycopg.types.json.Json(task.citations),
                    task.reason,
                ),
            )
        conn.commit()
    existing = _fetch_task_db(task.provider_msg_id)
    return bool(existing and existing.status == "pending")


def _approve_task_db(provider_msg_id: str, reviewer: str, final_reply: str) -> None:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE review_tasks
                SET status = 'approved', reviewer = %s, final_reply = %s, updated_at = NOW()
                WHERE provider_msg_id = %s AND status = 'pending'
                """,
                (reviewer, final_reply, provider_msg_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="task already processed or not found")

            # Write back to primary business tables.
            cur.execute(
                """
                UPDATE emails
                SET status = 'replied', updated_at = NOW()
                WHERE provider_msg_id = %s
                """,
                (provider_msg_id,),
            )
            cur.execute(
                """
                UPDATE replies r
                SET
                    final_text = %s,
                    reviewer = %s,
                    sent_at = NOW()
                FROM emails e
                WHERE
                    e.id = r.email_id
                    AND e.provider_msg_id = %s
                    AND r.created_at = (
                        SELECT MAX(r2.created_at)
                        FROM replies r2
                        WHERE r2.email_id = e.id
                    )
                """,
                (final_reply, reviewer, provider_msg_id),
            )
            cur.execute(
                """
                INSERT INTO audit_events(email_id, event_type, payload, created_at)
                SELECT
                    e.id,
                    'review_approved',
                    %s::jsonb,
                    NOW()
                FROM emails e
                WHERE e.provider_msg_id = %s
                """,
                (
                    psycopg.types.json.Json(
                        {"provider_msg_id": provider_msg_id, "reviewer": reviewer, "action": "approved"}
                    ),
                    provider_msg_id,
                ),
            )
        conn.commit()


def _reject_task_db(provider_msg_id: str, reviewer: str, reason: str) -> None:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE review_tasks
                SET status = 'rejected', reviewer = %s, reject_reason = %s, updated_at = NOW()
                WHERE provider_msg_id = %s AND status = 'pending'
                """,
                (reviewer, reason, provider_msg_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=400, detail="task already processed or not found")

            cur.execute(
                """
                UPDATE emails
                SET status = 'closed', updated_at = NOW()
                WHERE provider_msg_id = %s
                """,
                (provider_msg_id,),
            )
            cur.execute(
                """
                UPDATE replies r
                SET reviewer = %s
                FROM emails e
                WHERE
                    e.id = r.email_id
                    AND e.provider_msg_id = %s
                    AND r.created_at = (
                        SELECT MAX(r2.created_at)
                        FROM replies r2
                        WHERE r2.email_id = e.id
                    )
                """,
                (reviewer, provider_msg_id),
            )
            cur.execute(
                """
                INSERT INTO audit_events(email_id, event_type, payload, created_at)
                SELECT
                    e.id,
                    'review_rejected',
                    %s::jsonb,
                    NOW()
                FROM emails e
                WHERE e.provider_msg_id = %s
                """,
                (
                    psycopg.types.json.Json(
                        {
                            "provider_msg_id": provider_msg_id,
                            "reviewer": reviewer,
                            "action": "rejected",
                            "reason": reason,
                        }
                    ),
                    provider_msg_id,
                ),
            )
        conn.commit()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME, "store_backend": STORE_BACKEND}


@app.post("/review/queue")
def queue_review(task: ReviewTaskIn) -> Dict[str, Any]:
    if _db_enabled():
        queued = _queue_task_db(task)
        return {"ok": True, "queued": queued, "provider_msg_id": task.provider_msg_id}

    existing = TASKS.get(task.provider_msg_id)
    if existing and existing.status == "pending":
        return {"ok": True, "queued": False, "provider_msg_id": task.provider_msg_id}
    now = _now_iso()
    TASKS[task.provider_msg_id] = ReviewTask(**task.model_dump(), created_at=now, updated_at=now)
    return {"ok": True, "queued": True, "provider_msg_id": task.provider_msg_id}


@app.get("/review/tasks")
def list_tasks(status: Optional[str] = "pending") -> Dict[str, Any]:
    if _db_enabled():
        items = _list_tasks_db(status)
        return {"count": len(items), "items": items}
    items = list(TASKS.values())
    if status:
        items = [t for t in items if t.status == status]
    return {"count": len(items), "items": items}


@app.get("/review/tasks/{provider_msg_id}")
def get_task(provider_msg_id: str) -> ReviewTask:
    if _db_enabled():
        task = _fetch_task_db(provider_msg_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        return task
    task = TASKS.get(provider_msg_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.post("/review/tasks/{provider_msg_id}/approve")
def approve_task(provider_msg_id: str, payload: ApproveRequest) -> Dict[str, Any]:
    task = get_task(provider_msg_id)
    if task.status != "pending":
        raise HTTPException(status_code=400, detail="task already processed")

    final_reply = payload.final_reply or task.draft
    send_result = _send_email(
        to_addr=str(task.from_addr),
        subject=task.subject,
        body_text=final_reply,
        provider_msg_id=provider_msg_id,
    )

    if _db_enabled():
        _approve_task_db(provider_msg_id, payload.reviewer, final_reply)
        return {"ok": True, "provider_msg_id": provider_msg_id, "send_result": send_result}

    task.status = "approved"
    task.reviewer = payload.reviewer
    task.final_reply = final_reply
    task.updated_at = _now_iso()
    TASKS[provider_msg_id] = task
    return {"ok": True, "provider_msg_id": provider_msg_id, "send_result": send_result}


@app.post("/review/tasks/{provider_msg_id}/reject")
def reject_task(provider_msg_id: str, payload: RejectRequest) -> Dict[str, Any]:
    task = get_task(provider_msg_id)
    if task.status != "pending":
        raise HTTPException(status_code=400, detail="task already processed")

    reason = payload.reason or ""
    if _db_enabled():
        _reject_task_db(provider_msg_id, payload.reviewer, reason)
        return {"ok": True, "provider_msg_id": provider_msg_id, "reason": reason}

    task.status = "rejected"
    task.reviewer = payload.reviewer
    task.reject_reason = reason
    task.updated_at = _now_iso()
    TASKS[provider_msg_id] = task
    return {"ok": True, "provider_msg_id": provider_msg_id, "reason": reason}


@app.get("/review/ui", response_class=HTMLResponse)
def review_ui() -> str:
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>AgentMail Review UI</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 24px; }
      .task { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin: 12px 0; }
      .meta { color: #555; font-size: 13px; }
      textarea { width: 100%; min-height: 120px; }
      button { margin-right: 8px; }
    </style>
  </head>
  <body>
    <h2>AgentMail Human Review</h2>
    <p class="meta">Endpoint: /review/tasks?status=pending</p>
    <button onclick="loadTasks()">Refresh</button>
    <div id="tasks"></div>
    <script>
      async function loadTasks() {
        const res = await fetch('/review/tasks?status=pending');
        const data = await res.json();
        const root = document.getElementById('tasks');
        root.innerHTML = '';
        for (const t of data.items) {
          const el = document.createElement('div');
          el.className = 'task';
          const draftId = `draft-${t.provider_msg_id}`;
          el.innerHTML = `
            <div><strong>${t.subject || '(no subject)'}</strong></div>
            <div class="meta">msg: ${t.provider_msg_id} | from: ${t.from_addr} | confidence: ${t.confidence}</div>
            <p>${t.reason}</p>
            <textarea id="${draftId}">${t.draft || ''}</textarea><br/>
            <button onclick="approve('${t.provider_msg_id}', '${draftId}')">Approve & Send</button>
            <button onclick="reject('${t.provider_msg_id}')">Reject</button>
          `;
          root.appendChild(el);
        }
      }

      async function approve(msgId, draftId) {
        const finalReply = document.getElementById(draftId).value;
        await fetch(`/review/tasks/${msgId}/approve`, {
          method: 'POST',
          headers: {'content-type': 'application/json'},
          body: JSON.stringify({reviewer: 'ui-reviewer', final_reply: finalReply})
        });
        await loadTasks();
      }

      async function reject(msgId) {
        await fetch(`/review/tasks/${msgId}/reject`, {
          method: 'POST',
          headers: {'content-type': 'application/json'},
          body: JSON.stringify({reviewer: 'ui-reviewer', reason: 'Rejected from UI'})
        });
        await loadTasks();
      }

      loadTasks();
    </script>
  </body>
</html>
"""


@app.post("/mock/agentmail/send")
def mock_agentmail_send(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, "accepted": True, "echo": payload}
