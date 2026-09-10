"""FastAPI application entry point for ContextMesh."""

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from apps.api.routes import router
from observability.logging import setup_logging, get_logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

setup_logging()
logger = get_logger("apps.api")

app = FastAPI(
    title="ContextMesh API",
    description="Permission-aware Agentic Retrieval System for Enterprise Intelligence across Jira, Slack, and Gmail.",
    version="0.1.0"
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = round((time.time() - start_time) * 1000, 2)
    logger.info(f"{request.method} {request.url.path} [{response.status_code}] - {duration}ms")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=port, reload=True)
