"""Run with ``python -m api.main``; requests never enter the access log."""

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Path as PathParameter
from pydantic import BaseModel, Field, StrictBool, field_validator
from starlette.datastructures import Headers
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from api.frontend import FrontendFiles, slide_file
from api.slides import render_slide_page
from api.speech import SpeechRequest, synthesize
from rag.answer import Answerer, ConfigurationError, HistoryTurn, configure_private_logging
from rag.config import ROOT, Settings


class APIKeyMiddleware:
    """Authenticate before routing, redirects, method checks, or body parsing."""

    def __init__(self, app: ASGIApp, api_key: str, frontend: FrontendFiles | None = None):
        self.app = app
        self.api_key = api_key
        self.frontend = frontend

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope["path"] == "/health" and scope["method"] == "GET"):
            await self.app(scope, receive, send)
            return
        if (scope["method"] in {"GET", "HEAD"} and self.frontend is not None
                and self.frontend.lookup(scope["path"]) is not None):
            await self.app(scope, receive, send)
            return
        if not self.api_key:
            response = JSONResponse(status_code=503, content={"detail": "API authentication is not configured."})
        else:
            authorization = Headers(scope=scope).get("authorization", "")
            scheme, _, key = authorization.partition(" ")
            if scheme.lower() == "bearer" and secrets.compare_digest(key.encode(), self.api_key.encode()):
                await self.app(scope, receive, send)
                return
            response = JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."},
                                    headers={"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)


class TurnRequest(BaseModel):
    question: str = Field(max_length=20000)
    answer: str = Field(max_length=100000)


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    history: list[TurnRequest] = Field(default_factory=list, max_length=100)

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A question is required.")
        return value

    def turns(self) -> list[HistoryTurn]:
        return [HistoryTurn(t.question, t.answer) for t in self.history[-5:]]


class AskRequest(QuestionRequest):
    rag_enabled: StrictBool = True


def create_app(settings: Settings | None = None, answerer: Any = None, *,
               frontend_dir: Path | None = None, slides_dir: Path | None = None) -> FastAPI:
    configure_private_logging()
    config = settings or Settings.load()
    frontend = FrontendFiles(frontend_dir if frontend_dir is not None else ROOT / "frontend/dist")
    slides = (slides_dir if slides_dir is not None else ROOT / "data/raw/slides").resolve()
    # API schema/docs stay disabled. Only health and inventoried build assets are public.
    application = FastAPI(title="Course RAG", docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(APIKeyMiddleware, api_key=config.api_key, frontend=frontend)
    application.state.answerer = answerer

    def service() -> Answerer:
        if application.state.answerer is None:
            application.state.answerer = Answerer(config)
        return application.state.answerer

    def dispatch(action: str, request: QuestionRequest, **options: Any) -> dict[str, Any]:
        try:
            return getattr(service(), action)(request.question, request.turns(), **options)
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail="DeepSeek settings are incomplete.") from exc
        except Exception:
            # Upstream exceptions may include request text or credentials. Neither
            # expose their bodies to the caller nor log them on this service.
            raise HTTPException(status_code=502, detail="A required upstream service failed.") from None

    @application.get("/health")
    def health() -> dict[str, Any]:
        llm_configured = bool(config.llm_base_url and config.llm_model and config.llm_api_key)
        try:
            dependencies = service().retriever.health()
        except Exception:
            dependencies = {"ok": False}
        ready = bool(config.api_key and llm_configured and dependencies.get("ok"))
        return {"status": "ok" if ready else "degraded", "ready": ready,
                "llm_configured": llm_configured, "retrieval": dependencies}

    @application.post("/ask")
    def ask(request: AskRequest) -> dict[str, Any]:
        return dispatch("ask", request, rag_enabled=request.rag_enabled)

    @application.post("/retrieve")
    def retrieve(request: QuestionRequest) -> dict[str, Any]:
        return dispatch("retrieve", request)

    @application.get("/auth")
    def auth() -> dict[str, bool]:
        return {"ok": True}

    @application.post("/speech")
    async def speech(request: SpeechRequest) -> Response:
        audio = await synthesize(request, config)
        return Response(audio, media_type="audio/wav", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    @application.get("/slides/{filename}")
    def slides_pdf(filename: str) -> FileResponse:
        path = slide_file(slides, filename)
        return FileResponse(path, media_type="application/pdf", filename=filename,
                            content_disposition_type="inline", headers={
                                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                            })

    @application.get("/slides/{filename}/pages/{page}")
    def slides_page(filename: str, page: int = PathParameter(ge=1)) -> Response:
        png = render_slide_page(slides, filename, page)
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    frontend.register(application)
    return application


app = create_app()


def main() -> None:
    import uvicorn
    settings = Settings.load()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port, access_log=False)


if __name__ == "__main__":
    main()
