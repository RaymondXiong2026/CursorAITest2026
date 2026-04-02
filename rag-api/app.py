import hashlib
import os
import uuid
from typing import Any, Dict, List, Literal

from fastapi import FastAPI, HTTPException
from langdetect import LangDetectException, detect
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models

load_dotenv()


CATEGORY_TYPE = Literal["auto_reply", "review", "escalate", "archive", "spam"]
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))


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
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "kb_chunks")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_IN_MEMORY = os.getenv("QDRANT_IN_MEMORY", "false").lower() == "true"

app = FastAPI(title=APP_NAME, version="0.1.1")
qdrant = QdrantClient(":memory:") if QDRANT_IN_MEMORY else QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def _embed_text(text: str) -> List[float]:
    if not text:
        return [0.0] * EMBEDDING_DIM
    vec = [0.0] * EMBEDDING_DIM
    words = text.lower().split()
    for token in words:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for i in range(0, min(len(digest), EMBEDDING_DIM)):
            value = digest[i] / 255.0
            vec[i] += value
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
    if v in {"zh-cn", "zh", "zh-hans"}:
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
        category = "spam"
        priority = "low"
        risk_score = 0.95
        reasons.append("spam_keyword")
        return ClassifyResponse(category=category, priority=priority, risk_score=risk_score, reasons=reasons)

    if any(w in text for w in escalate_words):
        category = "escalate"
        priority = "high"
        risk_score = 0.9
        reasons.append("legal_or_security_risk")
        return ClassifyResponse(category=category, priority=priority, risk_score=risk_score, reasons=reasons)

    if any(w in text for w in review_words):
        category = "review"
        priority = "high"
        risk_score = 0.75
        reasons.append("high_risk_business_topic")
        return ClassifyResponse(category=category, priority=priority, risk_score=risk_score, reasons=reasons)

    if any(w in text for w in archive_words):
        category = "archive"
        priority = "low"
        risk_score = 0.2
        reasons.append("notification_like_email")
        return ClassifyResponse(category=category, priority=priority, risk_score=risk_score, reasons=reasons)

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
    points = result.points

    citations: List[Citation] = []
    for p in points:
        payload = p.payload or {}
        chunk_text = str(payload.get("content", ""))[:300]
        citations.append(
            Citation(
                id=str(p.id),
                source=str(payload.get("source", "unknown")),
                score=float(p.score),
                snippet=chunk_text,
            )
        )
    return citations


def _build_reply(question: str, language: str, citations: List[Citation]) -> str:
    if language.startswith("zh"):
        if citations:
            evidence = "\n".join([f"- {c.snippet}" for c in citations[:3]])
            return (
                "您好，感谢您的来信。\n\n"
                "根据我们现有知识库信息，给您以下建议：\n"
                f"{evidence}\n\n"
                "如果您希望，我可以继续为您整理成具体操作步骤。"
            )
        return "您好，感谢您的来信。当前知识库证据不足，建议转人工客服进一步确认后回复您。"

    if citations:
        evidence = "\n".join([f"- {c.snippet}" for c in citations[:3]])
        return (
            "Hello, and thank you for your message.\n\n"
            "Based on our current knowledge base, here is the best guidance:\n"
            f"{evidence}\n\n"
            "If helpful, I can also provide step-by-step actions for your specific case."
        )
    return "Hello, thank you for your email. We do not have sufficient evidence in the knowledge base, so this should be routed for human review."


@app.on_event("startup")
def startup() -> None:
    # Ensure collection exists for first run in memory or external Qdrant.
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
        vector = _embed_text(chunk.content)
        points.append(
            models.PointStruct(
                id=chunk_id,
                vector=vector,
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

    avg_score = 0.0
    if citations:
        avg_score = sum(c.score for c in citations) / len(citations)

    confidence = round(min(0.98, max(0.1, avg_score)), 2)
    review_required = cls.category in {"review", "escalate"} or confidence < 0.55 or not citations

    reply = _build_reply(payload.question, _norm_lang(payload.customer_language), citations)
    if review_required and payload.customer_language.startswith("zh"):
        reply += "\n\n（系统标记：该回复建议人工审核后发送）"
    elif review_required:
        reply += "\n\n(System note: human review is recommended before sending.)"

    return ReplyResponse(
        reply=reply,
        confidence=confidence,
        review_required=review_required,
        category=cls.category,
        citations=citations,
    )


@app.post("/translate", response_model=TranslateResponse)
def translate(payload: TranslateRequest) -> TranslateResponse:
    # Minimal local placeholder.
    # In production, replace with LibreTranslate/NLLB HTTP call.
    return TranslateResponse(
        translated_text=payload.text,
        source_lang=payload.source_lang,
        target_lang=payload.target_lang,
    )
