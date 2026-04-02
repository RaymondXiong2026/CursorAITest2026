# RAG API (FastAPI + Qdrant) for AgentMail

Minimal API used by the n8n workflow:

- `GET /health`
- `POST /detect-language`
- `POST /classify`
- `POST /kb/ingest`
- `POST /rag/reply`

## 1) Setup

```bash
cd rag-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2) Run

```bash
uvicorn app:app --reload --port 8000
```

## 3) Quick test

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/detect-language \
  -H 'content-type: application/json' \
  -d '{"text":"您好，我想咨询退款政策"}'
curl -s -X POST http://127.0.0.1:8000/classify \
  -H 'content-type: application/json' \
  -d '{"subject":"Need help", "body_text":"How can I reset password?"}'
```

## 4) Optional local LLM

If using Ollama:

```bash
ollama pull qwen2.5:7b-instruct
```

Set in `.env`:

```env
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen2.5:7b-instruct
```

