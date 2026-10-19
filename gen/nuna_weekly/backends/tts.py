"""
gen/nuna_weekly/backends/tts.py — TTS 백엔드 (Gemini Interactions / dummy)
==========================================================================
GeminiTTSBackend.synthesize(text, voice, style, out_wav) -> TTSResult
    - google-genai==2.31.0 client.interactions.create (unary, response_format audio)
    - 출력: interaction.output_audio.data (base64). 문서상 WAV(RIFF) 24kHz mono 16-bit.
      RIFF 헤더가 없으면 raw PCM s16le 로 간주해 wave 모듈로 감싼다.
    - 토큰: interaction.usage.total_input_tokens / total_output_tokens (SDK pydantic 모델 확인).
      값이 없으면 출력 토큰 = 초 × config.TTS_TOKENS_PER_SEC 로 추정.
DummyTTS — ffmpeg 사인톤(가청) 24kHz mono wav, 길이 len(text)/6.5+0.3 초. 0원·무네트워크.

키: secret_env.require("GEMINI_API_SUB_PAY_KEY"). 예외 메시지는 secret_env.redact 로 마스킹.
"""
from __future__ import annotations

import base64
import binascii
import logging
import subprocess
import wave
from pathlib import Path
from typing import Any

from .. import config, secret_env
from . import TTSResult

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.backends.tts")

PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1
PCM_SAMPWIDTH = 2  # s16le


class TTSError(RuntimeError):
    """TTS 호출·후처리 실패 (메시지는 마스킹됨)."""


def probe_seconds(path: Path | str) -> float:
    """ffprobe format=duration (초)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise TTSError(f"ffprobe duration 실패: {secret_env.redact(r.stderr, 200)}")
    try:
        return float((r.stdout or "").strip())
    except ValueError:
        raise TTSError(f"ffprobe duration 해석 실패: {secret_env.redact(r.stdout, 100)}") from None


def _write_pcm_as_wav(pcm: bytes, out_wav: Path, sample_rate: int, channels: int) -> None:
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(PCM_SAMPWIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def _int_or_none(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


class GeminiTTSBackend:
    name = "gemini"

    def __init__(self, client: Any = None, model: str | None = None) -> None:
        log.info("[NunaWeekly:backends.tts] v%s 시작", VERSION)
        key = secret_env.require("GEMINI_API_SUB_PAY_KEY")
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        self.client = client
        self.model = model or config.tts_model()

    def _create(self, text: str, voice: str, style: str) -> Any:
        return self.client.interactions.create(
            model=self.model,
            input=[{"type": "user_input",
                    "content": [{"type": "text", "text": text,
                                 "annotations": [{"type": "speech_metadata", "style": style}]}]}],
            response_format={"type": "audio"},
            generation_config={"speech_config": [{"voice": voice}]},
        )

    def synthesize(self, text: str, voice: str, style: str, out_wav: Path) -> TTSResult:
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        if not (text or "").strip():
            raise TTSError("TTS 입력 텍스트 비어있음")
        try:
            interaction = self._create(text, voice, style)
        except Exception as e:
            raise TTSError(secret_env.redact(f"Gemini TTS 호출 실패: {type(e).__name__}: {e}", 400)) from None

        audio = getattr(interaction, "output_audio", None)
        data = getattr(audio, "data", None) if audio is not None else None
        if not data:
            raise TTSError("Gemini TTS 응답에 output_audio.data 없음")
        try:
            raw = base64.b64decode(data, validate=False) if isinstance(data, str) else bytes(data)
        except (binascii.Error, ValueError, TypeError) as e:
            raise TTSError(f"오디오 base64 해독 실패: {type(e).__name__}") from None
        if not raw:
            raise TTSError("Gemini TTS 오디오 0바이트")

        if raw[:4] == b"RIFF":
            out_wav.write_bytes(raw)
        else:
            # 문서상 unary 는 WAV — 헤더가 없으면 raw PCM s16le 로 간주
            sr = _int_or_none(getattr(audio, "sample_rate", None)) or PCM_SAMPLE_RATE
            ch = _int_or_none(getattr(audio, "channels", None)) or PCM_CHANNELS
            _write_pcm_as_wav(raw, out_wav, sr, ch)
            log.info("TTS 응답 RIFF 헤더 없음 → PCM s16le %dHz %dch 로 감쌈", sr, ch)

        seconds = probe_seconds(out_wav)
        usage = getattr(interaction, "usage", None)
        in_tok = _int_or_none(getattr(usage, "total_input_tokens", None)) if usage is not None else None
        out_tok = _int_or_none(getattr(usage, "total_output_tokens", None)) if usage is not None else None
        if out_tok is None:
            out_tok = round(seconds * config.TTS_TOKENS_PER_SEC)
            log.info("TTS 출력 토큰 미보고 → 추정 %d (%.2fs × %d)", out_tok, seconds, config.TTS_TOKENS_PER_SEC)
        req_id = getattr(interaction, "id", None) or None
        log.info("Gemini TTS 완료: %.2fs in=%s out=%s id=%s", seconds, in_tok, out_tok, req_id)
        return TTSResult(path=out_wav, seconds=seconds, input_tokens=in_tok or 0,
                         output_tokens=out_tok, request_id=req_id)


class DummyTTS:
    name = "dummy"

    def __init__(self) -> None:
        log.info("[NunaWeekly:backends.tts] v%s 시작 (dummy)", VERSION)

    @staticmethod
    def duration_for(text: str) -> float:
        return round(len(text or "") / 6.5 + 0.3, 3)

    def synthesize(self, text: str, voice: str, style: str, out_wav: Path) -> TTSResult:
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        dur = self.duration_for(text)
        freq = 440 + (sum(ord(c) for c in (text or "")) % 200)
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:sample_rate={PCM_SAMPLE_RATE}:duration={dur}",
             "-ac", "1", "-c:a", "pcm_s16le", str(out_wav)],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise TTSError(f"더미 TTS 생성 실패: {r.stderr[:300]}")
        return TTSResult(path=out_wav, seconds=probe_seconds(out_wav), request_id="dummy-tts")
