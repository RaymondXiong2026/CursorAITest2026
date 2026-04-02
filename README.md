# CursorAITest2026

完整本地闭环包含：

- `n8n/workflow.agentmail-inbox-zero.json`：Inbox Zero + RAG + 人工审核工作流
- `rag-api/`：RAG API（支持可选 Ollama / LibreTranslate）
- `review-service/`：人工审核服务（入队、列表、批准发送、拒绝）
- `sql/init_agentmail_schema.sql`：数据库初始化脚本
- `docker-compose.yml`：一键启动编排

## 1) 一键启动（推荐）

```bash
cp .env.example .env
docker compose up -d
```

服务端口：

- n8n: `http://localhost:5678`
- rag-api: `http://localhost:8000/health`
- review-service: `http://localhost:8100/health`
- qdrant: `http://localhost:6333`
- postgres: `localhost:5432`

## 2) n8n 导入工作流

导入：

- `n8n/workflow.agentmail-inbox-zero.json`

工作流依赖环境变量（已在 compose 里注入）：

- `RAG_API_BASE_URL`
- `AGENTMAIL_SEND_URL`
- `HUMAN_REVIEW_QUEUE_URL`

## 3) 本地人工审核联调示例

1. n8n 低置信度任务会推送到：
   - `POST /review/queue`
2. 查看待审核队列：
   - `GET /review/tasks?status=pending`
3. 审核通过并发送：
   - `POST /review/tasks/{provider_msg_id}/approve`
4. 审核拒绝：
   - `POST /review/tasks/{provider_msg_id}/reject`

## 4) 无 Docker 单独运行

### rag-api

```bash
cd rag-api
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
```

### review-service

```bash
cd review-service
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m uvicorn app:app --host 0.0.0.0 --port 8100
```

