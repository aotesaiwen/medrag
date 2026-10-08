"""Resident, local-only CosyVoice worker. Run with .venv-tts/bin/python -m speech.server."""

from contextlib import asynccontextmanager
import asyncio
import secrets
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse, Response

from api.speech import SpeechRequest
from rag.config import Settings


def create_app(engine=None, settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.load()
    busy = Lock()

    @asynccontextmanager
    async def lifespan(app):
        if app.state.engine is None:
            from speech.engine import SpeechEngine
            app.state.engine = await asyncio.to_thread(SpeechEngine)
            await asyncio.to_thread(app.state.engine.synthesize, '你好，语音服务已经准备就绪。')
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if request.url.path == '/health' and request.method == 'GET':
            return await call_next(request)
        scheme, _, key = request.headers.get('authorization', '').partition(' ')
        if not config.api_key:
            return JSONResponse({'detail': 'Authentication is not configured.'}, status_code=503)
        if scheme.lower() != 'bearer' or not secrets.compare_digest(key.encode(), config.api_key.encode()):
            return JSONResponse({'detail': 'Invalid or missing API key.'}, status_code=401)
        return await call_next(request)

    @app.get('/health')
    def health():
        return {'ready': app.state.engine is not None, 'model': 'Fun-CosyVoice3-0.5B-2512',
                'sample_rate': 24000, 'target_seconds': 10}

    @app.post('/synthesize')
    def synthesize(request: SpeechRequest):
        if app.state.engine is None:
            raise HTTPException(503, 'Speech is not ready.')
        if not busy.acquire(blocking=False):
            raise HTTPException(429, 'Speech is busy. Retry shortly.', headers={'Retry-After': '2'})
        try:
            audio, elapsed, duration = app.state.engine.synthesize(request.text)
            return Response(audio, media_type='audio/wav', headers={
                'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'Server-Timing': f'synthesis;dur={elapsed * 1000:.1f}',
                'X-Audio-Duration': f'{duration:.3f}',
            })
        except Exception:
            raise HTTPException(502, 'Speech synthesis failed.') from None
        finally:
            busy.release()

    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=8104, access_log=False)


if __name__ == '__main__':
    main()
