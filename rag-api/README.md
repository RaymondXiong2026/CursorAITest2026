# RAG API (FastAPI + Qdrant) for AgentMail

Production-ready starter API used by the n8n workflow:

- `GET /health`
- `POST /detect-language`
- `POST /classify`
- `POST /kb/ingest`
- `POST /rag/reply`
- `POST /translate`

Supports:

- Qdrant in-memory mode for quick local run
- Ollama generation (`LLM_PROVIDER=ollama`)
- LibreTranslate translation (optional, with graceful fallback)

## 1) Setup

```bash
cd rag-api
python3 -m pip install -r requirements.txt
cp .env.example .env
```

## 2) Run

```bash
python3 -m uvicorn app:app --reload --port 8000
```

## 3) Key environment variables

```env
# Qdrant
QDRANT_IN_MEMORY=true
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=kb_chunks

# LLM
LLM_PROVIDER=template   # template | ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct

# Translation
ENABLE_TRANSLATION=false
LIBRETRANSLATE_URL=http://localhost:5000
```

## 4) Quick test

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/detect-language \
  -H 'content-type: application/json' \
  -d '{"text":"您好，我想咨询退款政策"}'
curl -s -X POST http://127.0.0.1:8000/classify \
  -H 'content-type: application/json' \
  -d '{"subject":"Refund request","body_text":"I need refund due to outage"}'

# ingest sample text then ask RAG
python3 scripts/ingest_sample.py --api http://127.0.0.1:8000 ../README.md
curl -s -X POST http://127.0.0.1:8000/rag/reply \
  -H 'content-type: application/json' \
  -d '{"subject":"产品问题","question":"这个项目是做什么的？","customer_language":"zh-cn","max_chunks":3}'
```

## 5) Enable Ollama

```bash
ollama pull qwen2.5:7b-instruct
```

Then update `.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
```

## 6) Enable LibreTranslate (optional)

Run LibreTranslate service and set:

```env
ENABLE_TRANSLATION=true
LIBRETRANSLATE_URL=http://localhost:5000
```

## 7) Database schema for n8n workflow

Initialize PostgreSQL tables used by the workflow:

```bash
psql "$DATABASE_URL" -f ../sql/init_agentmail_schema.sql
```

