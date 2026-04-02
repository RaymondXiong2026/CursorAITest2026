# CursorAITest2026

## Deliverables included

- `n8n/workflow.agentmail-inbox-zero.json`  
  Importable n8n workflow for AgentMail Inbox Zero orchestration.
- `rag-api/`  
  Minimal FastAPI + Qdrant RAG service used by the n8n workflow.

## Quick start

```bash
cd rag-api
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload --port 8000
```

Then import the n8n workflow JSON and set these env vars in n8n:

- `RAG_API_BASE_URL=http://<your-rag-api-host>:8000`
- `AGENTMAIL_SEND_URL=<your-agentmail-send-endpoint>`
- `HUMAN_REVIEW_QUEUE_URL=<your-review-queue-endpoint>` (optional)

Initialize database tables (recommended before running n8n workflow):

```bash
psql "$DATABASE_URL" -f sql/init_agentmail_schema.sql
```
