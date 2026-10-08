import asyncio
import io
import threading
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.main import create_app
from api.speech import SpeechRequest, synthesize, validate_wav
from rag.config import Settings
from speech.server import create_app as create_worker
from speech.text import clean_text


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\x01\x00' * 240)
    return buffer.getvalue()


HEADERS = {'Authorization': 'Bearer speech-test'}


def test_text_is_not_truncated_or_summarized():
    text = '数据删除条件。' * 100
    assert clean_text(text) == text
    assert clean_text('  <|endofprompt|>\x00你好\n世界  ') == '你好 世界'


@pytest.mark.parametrize('text', ['', '  ', '<|endofprompt|>', '...！', '文' * 6001])
def test_invalid_speech_input(text):
    with pytest.raises(ValueError):
        SpeechRequest(text=text)


def test_gateway_authentication_and_audio_headers(monkeypatch):
    upstream = AsyncMock(return_value=wav_bytes())
    monkeypatch.setattr('api.main.synthesize', upstream)
    client = TestClient(create_app(Settings(api_key='speech-test')))
    assert client.post('/speech', json={'text': '你好'}).status_code == 401
    upstream.assert_not_called()
    response = client.post('/speech', headers=HEADERS, json={'text': '你好'})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'audio/wav'
    assert response.headers['cache-control'] == 'no-store'
    validate_wav(response.content)


@pytest.mark.parametrize('data', [b'', b'not audio', b'RIFF' + b'\0' * 40])
def test_invalid_audio_is_rejected(data):
    with pytest.raises(HTTPException) as error:
        validate_wav(data)
    assert error.value.status_code == 502


def test_truncated_audio_is_rejected():
    with pytest.raises(HTTPException):
        validate_wav(wav_bytes()[:-10])


@pytest.mark.parametrize('status, expected', [(401, 502), (500, 502), (429, 429), (503, 503), (504, 504)])
def test_gateway_hides_upstream_errors(monkeypatch, status, expected):
    original_client = httpx.AsyncClient
    def transport(request):
        assert request.headers['authorization'] == 'Bearer speech-test'
        return httpx.Response(status, text='private question and secret credential')
    monkeypatch.setattr('api.speech.httpx.AsyncClient', lambda **kwargs:
                        original_client(transport=httpx.MockTransport(transport), **kwargs))
    with pytest.raises(HTTPException) as error:
        asyncio.run(synthesize(SpeechRequest(text='测试'), Settings(api_key='speech-test')))
    assert error.value.status_code == expected
    assert 'private' not in error.value.detail


def test_gateway_total_deadline(monkeypatch):
    original_client = httpx.AsyncClient
    async def delayed(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, content=wav_bytes(), headers={'content-type': 'audio/wav'})
    monkeypatch.setattr('api.speech.httpx.AsyncClient', lambda **kwargs:
                        original_client(transport=httpx.MockTransport(delayed), **kwargs))
    with pytest.raises(HTTPException) as error:
        asyncio.run(synthesize(SpeechRequest(text='测试'), Settings(speech_timeout=0.01)))
    assert error.value.status_code == 504


def test_worker_preserves_private_text_and_releases_lock_after_error(caplog):
    def fail(text):
        raise RuntimeError('private speech text')
    engine = SimpleNamespace(synthesize=fail)
    client = TestClient(create_worker(engine, Settings(api_key='speech-test')))
    assert client.post('/synthesize', json={'text': '测试'}).status_code == 401
    response = client.post('/synthesize', headers=HEADERS, json={'text': '测试'})
    assert response.status_code == 502
    assert 'private' not in response.text + caplog.text
    engine.synthesize = lambda text: (wav_bytes(), 0.5, 0.01)
    response = client.post('/synthesize', headers=HEADERS, json={'text': '测试'})
    assert response.status_code == 200
    assert response.headers['server-timing'] == 'synthesis;dur=500.0'


def test_worker_rejects_overlapping_synthesis_without_queueing():
    started, finish = threading.Event(), threading.Event()
    def generate(text):
        started.set()
        assert finish.wait(5)
        return wav_bytes(), 0.5, 0.01
    client = TestClient(create_worker(SimpleNamespace(synthesize=generate), Settings(api_key='speech-test')))
    responses = []
    thread = threading.Thread(target=lambda: responses.append(client.post('/synthesize', headers=HEADERS, json={'text': '测试'})))
    thread.start()
    try:
        assert started.wait(5)
        assert client.post('/synthesize', headers=HEADERS, json={'text': '另一段'}).status_code == 429
    finally:
        finish.set(); thread.join(5)
    assert responses[0].status_code == 200
