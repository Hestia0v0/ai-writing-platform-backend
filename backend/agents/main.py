"""
Agents Service — FastAPI application
Exposes the 5 core AI agent endpoints:

  POST /agent/guardrail   → Security Guardrail Agent
  POST /agent/generate    → Drafting & Generation Agent
  POST /agent/evaluate    → Evaluation Panel (3 concurrent sub-agents + Master Judge)
  POST /agent/refine      → Refinement & Polishing Agent
  POST /agent/recommend   → Knowledge Retrieval RAG Agent
"""
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from routers import guardrail, generate, evaluate, refine, recommend  # noqa: F401
from dependencies import (
    get_drafting,
    get_evaluation_panel,
    get_guardrail,
    get_knowledge_rag,
    get_refinement,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eagerly initialise all agent singletons at startup so the first
    # real request is not slowed down by lazy initialisation.
    get_guardrail()
    get_drafting()
    get_evaluation_panel()
    get_refinement()
    get_knowledge_rag()
    yield


app = FastAPI(
    title="AI Writing Platform — Agents Service",
    version="1.0.0",
    description=(
        "Multi-agent AI backend for the AI Writing Platform.\n\n"
        "Provides five specialised agents:\n"
        "- **Security Guardrail** — prompt-injection & content filter\n"
        "- **Drafting** — structured essay generation\n"
        "- **Evaluation Panel** — concurrent multi-agent scoring (0–100)\n"
        "- **Refinement** — voice-preserving polishing with diff output\n"
        "- **Knowledge RAG** — vocabulary & idiom recommendations\n"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(guardrail.router, prefix="/agent", tags=["guardrail"])
app.include_router(generate.router, prefix="/agent", tags=["drafting"])
app.include_router(evaluate.router, prefix="/agent", tags=["evaluation"])
app.include_router(refine.router, prefix="/agent", tags=["refinement"])
app.include_router(recommend.router, prefix="/agent", tags=["knowledge-rag"])


@app.get("/", tags=["health"])
async def root():
    return {
        "service": "agents",
        "status": "ok",
        "version": "1.0.0",
        "endpoints": [
            "POST /agent/guardrail",
            "POST /agent/generate",
            "POST /agent/evaluate",
            "POST /agent/refine",
            "POST /agent/recommend",
        ],
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}
