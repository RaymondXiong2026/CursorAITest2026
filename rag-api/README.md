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
python3 -m pip install -r requirements.txt
cp .env.example .env
```

## 2) Run

```bash
uvicorn app:app --reload --port 8000
```

By default, `.env.example` sets `QDRANT_IN_MEMORY=true` so you can run without Docker.
For external Qdrant, set:

```env
QDRANT_URL=http://localhost:6333
QDRANT_IN_MEMORY=false
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

# ingest sample text then ask RAG
python scripts/ingest_sample.py --api http://127.0.0.1:8000 ../README.md
curl -s -X POST http://127.0.0.1:8000/rag/reply \
  -H 'content-type: application/json' \
  -d '{"subject":"产品问题","question":"这个项目是做什么的？","customer_language":"zh-cn"}'
```

## 4) Notes

- This starter keeps generation deterministic and lightweight (template + retrieved evidence).
- For production, replace `_build_reply` with real LLM calls (Ollama/vLLM/OpenAI compatible).

