import pytest
from fastapi.testclient import TestClient

from backend import main, make_audio


class Clock:
    """Stand-in for main.now_ms that only moves when told to."""

    def __init__(self):
        self.t = 1_793_240_000_000

    def __call__(self) -> int:
        return self.t

    def advance(self, seconds: float):
        self.t += int(seconds * 1000)


@pytest.fixture(autouse=True)
def fake_tts(monkeypatch):
    """No test talks to edge-tts: synthesis writes a dummy file and records the text."""
    texts = []

    async def synthesize(text, path, voice, rate):
        texts.append(text)
        path.write_bytes(b"fake mp3")

    monkeypatch.setattr(make_audio, "synthesize", synthesize)
    return texts


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(main, "now_ms", c)
    return c


@pytest.fixture
def client(clock, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(main, "AUDIO_DIR", tmp_path / "audio")
    main.MATCH.clear()
    main.MATCH.update(main.default_match())
    main.CONNECTIONS.clear()
    with TestClient(main.app) as c:
        yield c
