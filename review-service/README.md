# Review Service (Human-in-the-loop)

FastAPI service with:

- review task queue endpoint for n8n
- approve/reject APIs
- optional Postgres persistence
- status write-back to `emails`/`replies`/`audit_events`
- minimal built-in web UI for reviewers

## Endpoints

- `GET /health`
- `POST /review/queue`
- `GET /review/tasks?status=pending`
- `GET /review/tasks/{provider_msg_id}`
- `POST /review/tasks/{provider_msg_id}/approve`
- `POST /review/tasks/{provider_msg_id}/reject`
- `GET /review/ui`
- `POST /mock/agentmail/send` (local test helper)

## Storage modes

- `STORAGE_BACKEND=postgres` (recommended)
- `STORAGE_BACKEND=memory` (quick local testing)

When using `postgres`, `DATABASE_URL` is required.

## Run

```bash
cd review-service
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m uvicorn app:app --host 0.0.0.0 --port 8100
```

Then open reviewer UI:

`http://127.0.0.1:8100/review/ui`

## Quick API flow

```bash
# 1) enqueue
curl -s -X POST http://127.0.0.1:8100/review/queue \
  -H 'content-type: application/json' \
  -d '{
    "provider_msg_id":"msg-001",
    "from_addr":"customer@example.com",
    "subject":"Need help",
    "draft":"Draft reply",
    "confidence":0.42
  }'

# 2) list pending
curl -s http://127.0.0.1:8100/review/tasks?status=pending

# 3) approve
curl -s -X POST http://127.0.0.1:8100/review/tasks/msg-001/approve \
  -H 'content-type: application/json' \
  -d '{"reviewer":"alice","final_reply":"Approved final reply"}'
```
