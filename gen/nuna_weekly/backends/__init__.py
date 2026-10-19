"""
gen/nuna_weekly/backends — 외부 호출 인터페이스 (실 구현 / dummy 구현)
=====================================================================
모든 외부 API 는 이 인터페이스 뒤에 있다. dummy 는 0원·무네트워크로 같은 산출물 형식을
만든다(전수테스트용). 선택: make_backends(dry: bool).

계약 (각 모듈이 구현):
    LLMBackend.complete_json(system, user, max_tokens) -> LLMResult
    TTSBackend.synthesize(text, voice, style, out_wav) -> TTSResult
    FalBackend.run(endpoint, payload, out_path, pending_path) -> FalResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

VERSION = "1.0.0"


@dataclass
class LLMResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str
    request_id: str | None = None


@dataclass
class TTSResult:
    path: Path
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str | None = None


@dataclass
class FalResult:
    path: Path
    remote_url: str
    request_id: str
    meta: dict[str, Any] = field(default_factory=dict)


class LLMBackend(Protocol):
    name: str

    def complete_json(self, system: str, user: str, max_tokens: int) -> LLMResult: ...


class TTSBackend(Protocol):
    name: str

    def synthesize(self, text: str, voice: str, style: str, out_wav: Path) -> TTSResult: ...


class FalBackend(Protocol):
    name: str

    def run(self, endpoint: str, payload: dict[str, Any], out_path: Path,
            pending_path: Path) -> FalResult: ...


@dataclass
class Backends:
    llm: Any
    tts: Any
    fal: Any
    is_dummy: bool


def make_backends(dry: bool) -> Backends:
    """dry=True → 전부 dummy(0원). 실 구현은 각 모듈에서 지연 import."""
    if dry:
        from .fal import DummyFal
        from .llm import DummyLLM
        from .tts import DummyTTS

        return Backends(DummyLLM(), DummyTTS(), DummyFal(), True)
    from .fal import FalQueueBackend
    from .llm import ClaudeBackend
    from .tts import GeminiTTSBackend

    return Backends(ClaudeBackend(), GeminiTTSBackend(), FalQueueBackend(), False)
