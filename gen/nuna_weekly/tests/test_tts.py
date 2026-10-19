"""tts.py · backends/tts.py 단위 테스트 — 가짜 클라이언트·DummyTTS, 네트워크 없음."""
from __future__ import annotations

import base64
import io
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from gen.nuna_weekly import config, tts
from gen.nuna_weekly.backends import TTSResult
from gen.nuna_weekly.backends.tts import DummyTTS, GeminiTTSBackend, TTSError
from gen.nuna_weekly.budget import Budget
from gen.nuna_weekly.db import MemoryStore
from gen.nuna_weekly.models import Script, ScriptSegment

EP = "2026-W43"
KEY = "AIza" + "SyTESTKEY" + "abcdefghijklmnopqrstu"  # 실키 형식 리터럴 회피(GitHub 비밀 스캔 오탐 방지)


def make_script(segs) -> Script:
    return Script(episode_id=EP, title="t", captions={},
                  segments=[ScriptSegment(s.seg_id, s.seconds, "가" * int(s.seconds * 5)) for s in segs])


def budget() -> Budget:
    return Budget(MemoryStore(), EP, fx_rate=1500.0)


def sample_rate(p: Path) -> int:
    with wave.open(str(p)) as w:
        return w.getframerate()


def pcm(seconds: float, sr: int = 24000) -> bytes:
    return b"\x10\x00" * int(seconds * sr)


def wav_bytes(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm(seconds))
    return buf.getvalue()


class FakeClient:
    def __init__(self, data: bytes, usage=None, exc: Exception | None = None):
        self.calls: list[dict] = []
        self.data, self.usage, self.exc = data, usage, exc
        self.interactions = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(id="int-1", usage=self.usage,
                               output_audio=SimpleNamespace(data=base64.b64encode(self.data).decode(),
                                                            sample_rate=None, channels=None))


def test_dummy_synthesize_and_fit_60(tmp_path):
    segs = config.segments(60)
    b = budget()
    assets = tts.synthesize_segments(make_script(segs), segs, DummyTTS(), b, tmp_path)
    assert [a.key for a in assets] == [f"tts_{s.seg_id}" for s in segs]
    for a, s in zip(assets, segs, strict=True):
        assert abs(tts.measure_seconds(a.local_path) - s.seconds) <= 0.02
        assert sample_rate(Path(a.local_path)) == 48000
    costs = b.store.costs
    assert len(costs) == len(segs) and all(c["state"] == "charged" and c["step"] == "tts" for c in costs)
    assert costs[0]["units"] and costs[0]["unit"] == "token"
    total = tts.concat_audio(assets, tmp_path / "voice.wav")
    assert abs(tts.measure_seconds(total) - 60) <= 0.1


class LongTTS:
    name = "long"

    def __init__(self, factor: float):
        self.factor = factor

    def synthesize(self, text, voice, style, out_wav):
        Path(out_wav).write_bytes(wav_bytes(self.target * self.factor))
        return TTSResult(path=Path(out_wav), seconds=self.target * self.factor, output_tokens=100)


def test_atempo_within_limit(tmp_path):
    seg = config.segments(60)[1]
    be = LongTTS(1.10)
    be.target = seg.seconds
    assets = tts.synthesize_segments(make_script([seg]), [seg], be, budget(), tmp_path)
    assert abs(assets[0].seconds - seg.seconds) <= 0.02


def test_fit_error_over_limit(tmp_path):
    seg = config.segments(60)[1]
    be = LongTTS(1.30)
    be.target = seg.seconds
    with pytest.raises(tts.TTSFitError) as ei:
        tts.synthesize_segments(make_script([seg]), [seg], be, budget(), tmp_path)
    assert ei.value.seg_id == seg.seg_id and ei.value.raw > ei.value.target


def test_gemini_riff_with_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_SUB_PAY_KEY", KEY)
    usage = SimpleNamespace(total_input_tokens=12, total_output_tokens=80)
    cl = FakeClient(wav_bytes(2.0), usage=usage)
    r = GeminiTTSBackend(client=cl).synthesize("안녕하세요", "Kore", "앵커", tmp_path / "a.wav")
    assert abs(r.seconds - 2.0) < 0.05 and r.input_tokens == 12 and r.output_tokens == 80
    call = cl.calls[0]
    assert call["model"] == config.tts_model()
    assert call["response_format"] == {"type": "audio"}
    assert call["generation_config"] == {"speech_config": [{"voice": "Kore"}]}
    ann = call["input"][0]["content"][0]["annotations"][0]
    assert ann == {"type": "speech_metadata", "style": "앵커"}


def test_gemini_raw_pcm_and_estimate(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_SUB_PAY_KEY", KEY)
    r = GeminiTTSBackend(client=FakeClient(pcm(1.5))).synthesize("x", "Kore", "s", tmp_path / "b.wav")
    assert (tmp_path / "b.wav").read_bytes()[:4] == b"RIFF"
    assert abs(r.seconds - 1.5) < 0.05
    assert r.output_tokens == round(r.seconds * config.TTS_TOKENS_PER_SEC)


def test_gemini_error_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_SUB_PAY_KEY", KEY)
    cl = FakeClient(b"", exc=RuntimeError(f"bad key {KEY} https://x.test/a?key=zzz"))
    with pytest.raises(TTSError) as ei:
        GeminiTTSBackend(client=cl).synthesize("x", "Kore", "s", tmp_path / "c.wav")
    msg = str(ei.value)
    assert KEY not in msg and "zzz" not in msg


def test_gemini_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_SUB_PAY_KEY", raising=False)
    from gen.nuna_weekly.secret_env import SecretMissingError

    with pytest.raises(SecretMissingError):
        GeminiTTSBackend(client=object())


def test_dummy_duration(tmp_path):
    r = DummyTTS().synthesize("가" * 65, "v", "s", tmp_path / "d.wav")
    assert abs(r.seconds - 10.3) < 0.05
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate,channels",
                          "-of", "csv=p=0", str(tmp_path / "d.wav")], capture_output=True, text=True).stdout
    assert out.strip() == "24000,1"
