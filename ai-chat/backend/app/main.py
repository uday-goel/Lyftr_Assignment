import logging
import math
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import llm
from .config import load_settings
from .db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

SIMULATABLE_STATUSES = {429, 500, 502, 503}


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str
    system_prompt: str | None = None

    @field_validator("session_id")
    @classmethod
    def _session_id_rules(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 128:
            raise ValueError("session_id must be 1-128 characters after trimming whitespace")
        return v

    @field_validator("message")
    @classmethod
    def _message_rules(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 4000:
            raise ValueError("message must be 1-4000 characters after trimming whitespace")
        return v

    @field_validator("system_prompt")
    @classmethod
    def _prompt_rules(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if len(v) > 4000:
            raise ValueError("system_prompt must be at most 4000 characters")
        return v or None


def parse_simulation(header: str | None) -> dict[str, int]:
    """'key_1=429,key_2=500' or '*=429' -> {'key_1': 429, ...}"""
    result: dict[str, int] = {}
    for pair in (header or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, sep, status = pair.partition("=")
        name, status = name.strip(), status.strip()
        if not sep or not name or not status.isdigit() or int(status) not in SIMULATABLE_STATUSES:
            raise HTTPException(
                status_code=422,
                detail="X-Simulate-Failure must be comma-separated <key_id>=<status> pairs; statuses: 429, 500, 502, 503",
            )
        result[name] = int(status)
    return result


def create_app() -> FastAPI:
    settings = load_settings()
    db = Database(settings.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()
        llm.setup_cache(settings)
        log.info("Started: model=%s keys=%d cache=%s", settings.model, len(settings.api_keys), settings.cache_type)
        yield

    app = FastAPI(title="AI Chat Backend", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        detail = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/health")
    async def health():
        db_ok = await run_in_threadpool(db.ping)
        keys = len(settings.api_keys)
        body = {"status": "ok" if db_ok and keys else "degraded", "keys_configured": keys,
                "cache_enabled": llm.cache_enabled()}
        return JSONResponse(body, status_code=200 if db_ok and keys else 503)

    @app.post("/chat")
    async def chat(body: ChatRequest, x_simulate_failure: str | None = Header(default=None)):
        started = time.perf_counter()
        simulate = parse_simulation(x_simulate_failure) if settings.enable_failure_simulation else {}

        stored_prompt = await run_in_threadpool(db.get_session_prompt, body.session_id)
        system_prompt = body.system_prompt or stored_prompt or settings.default_system_prompt
        history = await run_in_threadpool(db.get_history, body.session_id, settings.max_history_messages)

        messages = [{"role": "system", "content": system_prompt}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": body.message})

        try:
            result = await llm.complete(settings, messages, simulate)
        except llm.AllProvidersFailed:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        metadata = {
            "resolution_source": "LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
            "model": settings.model,
            "latency_ms": max(1, math.ceil((time.perf_counter() - started) * 1000)),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        await run_in_threadpool(db.save_exchange, body.session_id, body.message, result.reply,
                                metadata, body.system_prompt)
        return {"session_id": body.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    async def session_messages(session_id: str):
        if not await run_in_threadpool(db.session_exists, session_id):
            raise HTTPException(status_code=404, detail="session not found")
        history = await run_in_threadpool(db.get_history, session_id)
        return {"session_id": session_id, "messages": history}

    return app


app = create_app()
