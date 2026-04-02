# Review Service (Human-in-the-loop queue)

FastAPI service for human review flow:

- Accept review tasks from n8n
- List/retrieve pending tasks
- Approve task and send email through AgentMail endpoint
- Reject task with reviewer note

## Endpoints

- `GET /health`
- `POST /review/queue`
- `GET /review/tasks?status=pending`
- `GET /review/tasks/{provider_msg_id}`
- `POST /review/tasks/{provider_msg_id}/approve`
- `POST /review/tasks/{provider_msg_id}/reject`
- `POST /mock/agentmail/send` (local compose test helper)

## Run

```bash
cd review-service
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m uvicorn app:app --host 0.0.0.0 --port 8100
```

## Quick test

```bash
curl -s -X POST http://127.0.0.1:8100/review/queue \
  -H 'content-type: application/json' \
  -d '{
    "provider_msg_id":"msg-001",
    "from_addr":"customer@example.com",
    "subject":"Need help",
    "draft":"Draft reply",
    "confidence":0.42
  }'

curl -s http://127.0.0.1:8100/review/tasks

curl -s -X POST http://127.0.0.1:8100/review/tasks/msg-001/approve \
  -H 'content-type: application/json' \
  -d '{"reviewer":"alice","final_reply":"Approved final reply"}'
```
