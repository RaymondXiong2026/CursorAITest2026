import hashlib
import os
import uuid
from typing import Any, Dict, List, Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from langdetect import LangDetectException, detect
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models

load_dotenv()

CATEGORY_TYPE = Literal["auto_reply", "review", "escalate", "archive", "spam"]


class DetectLanguageRequest(BaseModel):
    text: str = Field(default="")


class DetectLanguageResponse(BaseModel):
    lang: str


class ClassifyRequest(BaseModel):
    subject: str = ""
    body_text: str = ""
    lang: str = "unknown"


class ClassifyResponse(BaseModel):
    category: CATEGORY_TYPE
    priority: Literal["low", "normal", "high"]
    risk_score: float
    reasons: List[str]


class IngestChunk(BaseModel):
    content: str
    source: str = "unknown"
    lang: str = "unknown"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    doc_id: str
    chunks: List[IngestChunk]


class IngestResponse(BaseModel):
    doc_id: str
    inserted: int


class ReplyRequest(BaseModel):
    subject: str = ""
    question: str
    customer_language: str = "zh-cn"
    max_chunks: int = 5


class Citation(BaseModel):
    id: str
    source: str
    score: float
    snippet: str


class ReplyResponse(BaseModel):
    reply: str
    confidence: float
    review_required: bool
    category: CATEGORY_TYPE
    citations: List[Citation]


class TranslateRequest(BaseModel):
    text: str
    source_lang: str = "auto"
    target_lang: str


class TranslateResponse(BaseModel):
    translated_text: str
    source_lang: str
    target_lang: str


APP_NAME = "agentmail-rag-api"
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
CONFIDENCE_THRESHOLD = float(os.getenv("RAG_CONFIDENCE_THRESHOLD", "0.55"))

COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "kb_chunks")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_IN_MEMORY = os.getenv("QDRANT_IN_MEMORY", "false").lower() == "true"

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "template").lower()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "30"))

ENABLE_TRANSLATION = os.getenv("ENABLE_TRANSLATION", "false").lower() == "true"
LIBRETRANSLATE_URL = os.getenv("LIBRETRANSLATE_URL", "http://localhost:5000")
LIBRETRANSLATE_API_KEY = os.getenv("LIBRETRANSLATE_API_KEY", "")
TRANSLATION_TIMEOUT_SECONDS = float(os.getenv("TRANSLATION_TIMEOUT_SECONDS", "20"))

app = FastAPI(title=APP_NAME, version="0.2.0")
qdrant = QdrantClient(":memory:") if QDRANT_IN_MEMORY else QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def _embed_text(text: str) -> List[float]:
    if not text:
        return [0.0] * EMBEDDING_DIM
    vec = [0.0] * EMBEDDING_DIM
    words = text.lower().split()
    for token in words:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(0, min(len(digest), EMBEDDING_DIM)):
            vec[i] += digest[i] / 255.0
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _ensure_collection() -> None:
    existing = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION_NAME in existing:
        return
    qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=EMBEDDING_DIM, distance=models.Distance.COSINE),
    )


def _norm_lang(raw: str) -> str:
    if not raw:
        return "unknown"
    v = raw.strip().lower()
    if v in {"zh", "zh-cn", "zh-hans"}:
        return "zh-cn"
    if v in {"en", "en-us", "en-gb"}:
        return "en"
    return v


def _detect_lang(text: str) -> str:
    if not text or not text.strip():
        return "unknown"
    try:
        return _norm_lang(detect(text))
    except LangDetectException:
        return "unknown"


def _classify(subject: str, body: str) -> ClassifyResponse:
    text = f"{subject}\n{body}".lower()
    reasons: List[str] = []
    risk_score = 0.1
    category: CATEGORY_TYPE = "auto_reply"
    priority: Literal["low", "normal", "high"] = "normal"

    spam_words = ["unsubscribe", "cheap", "free bitcoin", "viagra"]
    archive_words = ["newsletter", "webinar", "event reminder", "fyi"]
    escalate_words = ["lawsuit", "legal", "contract dispute", "security breach"]
    review_words = ["refund", "compensation", "invoice issue", "outage", "penalty"]

    if any(w in text for w in spam_words):
        return ClassifyResponse(category="spam", priority="low", risk_score=0.95, reasons=["spam_keyword"])
    if any(w in text for w in escalate_words):
        return ClassifyResponse(category="escalate", priority="high", risk_score=0.90, reasons=["legal_or_security_risk"])
    if any(w in text for w in review_words):
        return ClassifyResponse(category="review", priority="high", risk_score=0.75, reasons=["high_risk_business_topic"])
    if any(w in text for w in archive_words):
        return ClassifyResponse(category="archive", priority="low", risk_score=0.20, reasons=["notification_like_email"])
    reasons.append("default_auto_reply")
    return ClassifyResponse(category=category, priority=priority, risk_score=risk_score, reasons=reasons)


def _retrieve_chunks(query: str, limit: int = 5) -> List[Citation]:
    vector = _embed_text(query)
    result = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=limit,
        with_payload=True,
    )
    citations: List[Citation] = []
    for p in result.points:
        payload = p.payload or {}
        citations.append(
            Citation(
                id=str(p.id),
                source=str(payload.get("source", "unknown")),
                score=float(p.score),
                snippet=str(payload.get("content", ""))[:300],
            )
        )
    return citations


def _template_reply(language: str, citations: List[Citation], review_required: bool) -> str:
    if language.startswith("zh"):
        if citations:
            evidence = "\n".join([f"- {c.snippet}" for c in citations[:3]])
            text = (
                "您好，感谢您的来信。\n\n"
                "根据我们现有知识库信息，给您以下建议：\n"
                f"{evidence}\n\n"
                "如果您希望，我可以继续为您整理成具体操作步骤。"
            )
        else:
            text = "您好，感谢您的来信。当前知识库证据不足，建议转人工客服进一步确认后回复您。"
        if review_required:
            text += "\n\n（系统标记：该回复建议人工审核后发送）"
        return text

    if citations:
        evidence = "\n".join([f"- {c.snippet}" for c in citations[:3]])
        text = (
            "Hello, and thank you for your message.\n\n"
            "Based on our current knowledge base, here is the best guidance:\n"
            f"{evidence}\n\n"
            "If helpful, I can also provide step-by-step actions for your specific case."
        )
    else:
        text = "Hello, thank you for your email. We do not have sufficient evidence in the knowledge base, so this should be routed for human review."
    if review_required:
        text += "\n\n(System note: human review is recommended before sending.)"
    return text


def _translate_with_libretranslate(text: str, source_lang: str, target_lang: str) -> str:
    payload: Dict[str, Any] = {
        "q": text,
        "source": source_lang,
        "target": target_lang,
        "format": "text",
    }
    if LIBRETRANSLATE_API_KEY:
        payload["api_key"] = LIBRETRANSLATE_API_KEY
    with httpx.Client(timeout=TRANSLATION_TIMEOUT_SECONDS) as client:
        resp = client.post(f"{LIBRETRANSLATE_URL.rstrip('/')}/translate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("translatedText", text))


def _translate_text(text: str, source_lang: str, target_lang: str) -> str:
    if not text:
        return text
    if source_lang == target_lang:
        return text
    if not ENABLE_TRANSLATION:
        return text
    try:
        return _translate_with_libretranslate(text, source_lang, target_lang)
    except Exception:
        # Hard fail would block email reply pipeline; fallback keeps service available.
        return text


def _build_ollama_prompt(question: str, customer_lang: str, citations: List[Citation], review_required: bool) -> str:
    evidence = "\n".join([f"[{i+1}] {c.snippet}" for i, c in enumerate(citations[:5])]) if citations else "No evidence found."
    review_line = "Human review is required before send." if review_required else "Auto-send is allowed."
    return (
        "You are an enterprise support email assistant.\n"
        "Rules:\n"
        "1) Use only provided evidence, no fabrication.\n"
        "2) If evidence is insufficient, clearly say human confirmation is required.\n"
        "3) Keep tone professional and concise.\n"
        f"4) Output language: {customer_lang}\n"
        f"5) {review_line}\n\n"
        f"Customer question:\n{question}\n\n"
        f"Evidence:\n{evidence}\n\n"
        "Write the final reply email body only."
    )


def _generate_with_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    with httpx.Client(timeout=OLLAMA_TIMEOUT_SECONDS) as client:
        resp = client.post(f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", "")).strip()


def _build_reply(question: str, customer_language: str, citations: List[Citation], review_required: bool) -> str:
    lang = _norm_lang(customer_language)
    question_for_llm = question
    llm_lang = lang

    # If translation enabled and non-English, pivot to English for generation.
    if ENABLE_TRANSLATION and lang not in {"en", "unknown"}:
        question_for_llm = _translate_text(question, lang, "en")
        llm_lang = "en"

    if LLM_PROVIDER == "ollama":
        try:
            prompt = _build_ollama_prompt(question_for_llm, llm_lang, citations, review_required)
            llm_reply = _generate_with_ollama(prompt)
            if llm_reply:
                if ENABLE_TRANSLATION and lang not in {"en", "unknown"} and llm_lang == "en":
                    return _translate_text(llm_reply, "en", lang)
                return llm_reply
        except Exception:
            pass

    return _template_reply(lang, citations, review_required)


@app.on_event("startup")
def startup() -> None:
    _ensure_collection()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": APP_NAME}


@app.post("/detect-language", response_model=DetectLanguageResponse)
def detect_language(payload: DetectLanguageRequest) -> DetectLanguageResponse:
    return DetectLanguageResponse(lang=_detect_lang(payload.text))


@app.post("/classify", response_model=ClassifyResponse)
def classify(payload: ClassifyRequest) -> ClassifyResponse:
    return _classify(payload.subject, payload.body_text)


@app.post("/kb/ingest", response_model=IngestResponse)
def ingest(payload: IngestRequest) -> IngestResponse:
    if not payload.chunks:
        raise HTTPException(status_code=400, detail="chunks cannot be empty")
    points: List[models.PointStruct] = []
    for chunk in payload.chunks:
        chunk_id = str(uuid.uuid4())
        points.append(
            models.PointStruct(
                id=chunk_id,
                vector=_embed_text(chunk.content),
                payload={
                    "doc_id": payload.doc_id,
                    "content": chunk.content,
                    "source": chunk.source,
                    "lang": _norm_lang(chunk.lang),
                    "metadata": chunk.metadata,
                },
            )
        )
    qdrant.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)
    return IngestResponse(doc_id=payload.doc_id, inserted=len(points))


@app.post("/rag/reply", response_model=ReplyResponse)
def rag_reply(payload: ReplyRequest) -> ReplyResponse:
    cls = _classify(payload.subject, payload.question)
    citations = _retrieve_chunks(payload.question, limit=payload.max_chunks)
    avg_score = sum(c.score for c in citations) / len(citations) if citations else 0.0
    confidence = round(min(0.98, max(0.1, avg_score)), 2)
    review_required = cls.category in {"review", "escalate"} or confidence < CONFIDENCE_THRESHOLD or not citations
    reply = _build_reply(payload.question, payload.customer_language, citations, review_required)
    return ReplyResponse(
        reply=reply,
        confidence=confidence,
        review_required=review_required,
        category=cls.category,
        citations=citations,
    )


@app.post("/translate", response_model=TranslateResponse)
def translate(payload: TranslateRequest) -> TranslateResponse:
    translated = _translate_text(payload.text, payload.source_lang, payload.target_lang)
    return TranslateResponse(
        translated_text=translated,
        source_lang=payload.source_lang,
        target_lang=payload.target_lang,
    )
