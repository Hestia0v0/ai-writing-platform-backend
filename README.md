# AI Writing Platform — Backend

A Python microservices backend for the AI Writing Platform. Four FastAPI services sit behind a shared PostgreSQL + Redis infrastructure and are orchestrated with Docker Compose.

---

## Architecture Overview

```
ai-writing-platform-backend/
├── backend/
│   ├── api_gateway/        # Port 8000 — auth, quota, billing proxy
│   ├── ai_inference/       # Port 8001 — LLM calls, batch cache, HITL queue
│   ├── knowledge_retrieval/# Port 8002 — vector embeddings, semantic search
│   └── pipelines/          # Port 8003 — document parsing, workflow orchestration
├── infrastructure/
│   ├── docker-compose.yml  # Orchestrates all 7 containers
│   ├── .env.example        # Template for required secrets
│   └── init.sql            # PostgreSQL schema + pgvector setup
└── tests/
    ├── unit/               # Per-service unit tests
    ├── integration/        # Gateway routing integration tests
    └── performance/        # Locust load-testing scripts
```

### Service Map

```
Browser / Frontend
        │
        ▼  HTTP :8000
┌───────────────┐
│  API Gateway  │  JWT auth · daily quota · Stripe billing
└───────┬───────┘
        │ internal HTTP
   ┌────┴────────────────────────┐
   ▼                             ▼
┌──────────────┐      ┌──────────────────────┐
│ AI Inference │      │  Knowledge Retrieval │
│   :8001      │      │       :8002          │
│ DeepSeek LLM │      │ fastembed + pgvector │
│ batch cache  │      │ semantic search      │
│ HITL queue   │      └──────────────────────┘
└──────────────┘
        │
        ▼
┌──────────────┐
│  Pipelines   │
│    :8003     │
│ PDF/DOCX     │
│ parsing      │
└──────────────┘
        │
   ┌────┴──────────────────┐
   ▼                       ▼
PostgreSQL 16          Redis 7
+ pgvector             (3 logical DBs)
```

### Services

| Service | Port | Language | Responsibilities |
|---------|------|----------|-----------------|
| `api_gateway` | 8000 | Python 3.12 / FastAPI | Single entry point, JWT auth, daily quota enforcement (10 free / 100 basic / unlimited pro), Stripe webhooks |
| `ai_inference` | 8001 | Python 3.12 / FastAPI | DeepSeek LLM inference, prompt batch caching (Redis DB 0), human-in-the-loop review queue, rubric-based grading |
| `knowledge_retrieval` | 8002 | Python 3.12 / FastAPI | Document embedding (fastembed), HNSW vector index (pgvector), semantic similarity search |
| `pipelines` | 8003 | Python 3.12 / FastAPI | PDF (pypdf) and DOCX (python-docx) parsing, workflow state management (Redis DB 1) |
| `postgres` | 5432 | PostgreSQL 16 | Relational store for users, subscriptions, embeddings, pipeline results |
| `redis` | 6379 | Redis 7 | Shared cache / queue (DB 0: inference, DB 1: pipelines, DB 2: gateway) |

### Database Schema (init.sql)

| Table | Purpose |
|-------|---------|
| `users` | Identity and JWT credentials |
| `subscriptions` | Stripe plan tracking (free / basic / pro) |
| `document_embeddings` | Vector columns with HNSW index for fast ANN search |
| `pipeline_results` | Workflow outputs and status |

---

## Local Development

### Prerequisites

- Docker Desktop 4.x+ (with Compose V2)
- Git

### Quick Start

```bash
# 1. Clone and enter the infrastructure directory
cd infrastructure

# 2. Create the environment file
cp .env.example .env

# 3. Fill in required secrets in .env (see section below)

# 4. Build images and start all services
docker compose up --build
```

Services will be available at:

| Service | URL |
|---------|-----|
| API Gateway | http://localhost:8000 |
| AI Inference | http://localhost:8001 |
| Knowledge Retrieval | http://localhost:8002 |
| Pipelines | http://localhost:8003 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

Interactive API docs (Swagger UI) are served by each FastAPI service at `/docs`.

### Seed the Knowledge Base

```bash
docker compose exec knowledge_retrieval \
  python scripts/seed_knowledge.py
```

### Stop and Clean Up

```bash
docker compose down          # stop containers, keep volumes
docker compose down -v       # also remove postgres_data and redis_data
```

---

## Independent Service Development

Run individual services natively without Docker Compose — useful when iterating on a single service and wanting faster restart times.

### Start Infrastructure Only

Use Docker for the databases while services run directly on the host:

```bash
cd infrastructure
docker compose up postgres redis -d
```

PostgreSQL is exposed on host port **5458**, Redis on **6379**.

### Per-Service Setup

Each service follows the same pattern — replace `<service>` with one of `api_gateway`, `ai_inference`, `knowledge_retrieval`, or `pipelines`:

```bash
cd backend/<service>

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Set the environment variables listed below, then start the service:

```bash
uvicorn main:app --reload --port <port>
```

### Environment Variables per Service

**`api_gateway`** — port 8000
```bash
JWT_SECRET=dev-secret-change-in-prod
POSTGRES_DSN=postgresql://platform:platform@localhost:5458/platform
REDIS_URL=redis://localhost:6379/2
CORS_ORIGINS=http://localhost:5173
AI_INFERENCE_URL=http://localhost:8001
KNOWLEDGE_RETRIEVAL_URL=http://localhost:8002
PIPELINES_URL=http://localhost:8003
```

**`ai_inference`** — port 8001
```bash
DEEPSEEK_API_KEY=<your-key>
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://platform:platform@localhost:5458/platform
```

**`knowledge_retrieval`** — port 8002
```bash
POSTGRES_DSN=postgresql://platform:platform@localhost:5458/platform
```

**`pipelines`** — port 8003
```bash
REDIS_URL=redis://localhost:6379/1
POSTGRES_DSN=postgresql://platform:platform@localhost:5458/platform
AI_INFERENCE_URL=http://localhost:8001
KNOWLEDGE_RETRIEVAL_URL=http://localhost:8002
```

### Startup Order

Respect inter-service dependencies when running all services locally:

1. `postgres` + `redis` — infrastructure, no dependencies
2. `ai_inference` (8001) and `knowledge_retrieval` (8002) — independent of each other
3. `pipelines` (8003) — requires `ai_inference` and `knowledge_retrieval`
4. `api_gateway` (8000) — requires all three services above
5. Frontend dev server — requires `api_gateway`

---

## Environment Variables

Copy `.env.example` to `.env` in the `infrastructure/` directory and fill in the values.

| Variable | Description |
|----------|-------------|
| `DEEPSEEK_API_KEY` | API key for the DeepSeek LLM |
| `POSTGRES_USER` | PostgreSQL username (default: `platform`) |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_DB` | Database name (default: `platform`) |
| `REDIS_URL` | Redis connection string (default: `redis://redis:6379/0`) |
| `STRIPE_SECRET_KEY` | Stripe secret key (`sk_live_…` or `sk_test_…`) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret (`whsec_…`) |
| `STRIPE_PRICE_BASIC` | Stripe Price ID for the Basic plan |
| `STRIPE_PRICE_PRO` | Stripe Price ID for the Pro plan |

---

## Running Tests

Tests are located in the top-level `tests/` directory and use **pytest**.

```bash
# Run all unit tests
pytest tests/unit/

# Run a specific service's tests
pytest tests/unit/test_api_gateway.py

# Run integration tests (requires Docker Compose stack running)
pytest tests/integration/

# Run load tests (requires Locust)
locust -f tests/performance/locustfile.py --host http://localhost:8000
```

In CI, the full suite runs via GitHub Actions on every push to `main` or `develop`.

---

## CI/CD

The `.github/workflows/ci.yml` workflow contains four jobs:

| Job | Trigger | Steps |
|-----|---------|-------|
| `test-backend` | All pushes | pytest unit tests for all 4 services |
| `build-frontend` | All pushes | ESLint + Vite production build |
| `build-docker` | All pushes | Docker image build smoke test for all services |
| `integration-tests` | Push to `main` only | Full `docker compose up` + integration test suite |

---

## Project Structure per Service

Each service follows the same layout:

```
<service>/
├── main.py            # FastAPI app factory + middleware
├── requirements.txt   # Pinned dependencies
├── Dockerfile         # python:3.12-slim, exposes service port
├── routers/           # APIRouter modules (one file per domain)
├── core/              # Business logic (ai_inference only)
├── db/                # ORM models and session management
└── scripts/           # One-off admin scripts (knowledge_retrieval only)
```
