"""
gen/nuna_weekly/backends/llm.py — 대본 LLM 백엔드 (Claude 실 구현 / Dummy)
==========================================================================
    ClaudeBackend — anthropic SDK messages.create, 응답 텍스트를 JSON 으로 파싱.
    DummyLLM      — 0원·무네트워크. 사용자 프롬프트의 <facts>·<segments> JSON 을 읽어
                    script.validate 를 통과하는 결정적 대본을 만든다(전수테스트용).

비밀값은 secret_env.require 로만 읽고, 오류 메시지는 secret_env.redact 를 거친다.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from .. import config, secret_env
from . import LLMResult

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.backends.llm")

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?|\n?\s*```\s*$")
WEEKDAY_KO = ("월요일", "화요일", "수요일")


class LLMError(RuntimeError):
    """LLM 응답을 JSON 대본으로 해석할 수 없음."""


def parse_json_text(text: str) -> dict[str, Any]:
    """``` 펜스 제거 후 JSON 객체 파싱. 실패 시 LLMError(본문 미노출)."""
    body = _FENCE_RE.sub("", text or "").strip()
    try:
        data = json.loads(body)
    except ValueError as e:
        raise LLMError(f"LLM 응답 JSON 파싱 실패: {e.msg} (pos {e.pos})") from None
    if not isinstance(data, dict):
        raise LLMError("LLM 응답이 JSON 객체가 아님")
    return data


class ClaudeBackend:
    name = "claude"

    def __init__(self, client: Any = None) -> None:
        log.info("[NunaWeekly:llm] v%s 시작 · ClaudeBackend", VERSION)
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=secret_env.require("ANTHROPIC_API_KEY"),
                                         timeout=60, max_retries=2)
        self.client = client

    def complete_json(self, system: str, user: str, max_tokens: int) -> LLMResult:
        model = config.script_model()
        try:
            resp = self.client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                               messages=[{"role": "user", "content": user}])
        except Exception as e:
            raise LLMError(secret_env.redact(f"Claude 호출 실패: {type(e).__name__}: {e}", 300)) from None
        text = "".join(getattr(b, "text", "") for b in (resp.content or [])
                       if getattr(b, "type", "text") == "text")
        data = parse_json_text(text)
        usage = resp.usage
        return LLMResult(data=data, input_tokens=int(usage.input_tokens), output_tokens=int(usage.output_tokens),
                         model=model, request_id=getattr(resp, "id", None))


# --------------------------------------------------------------------------- #
# Dummy
# --------------------------------------------------------------------------- #
def _between(text: str, tag: str) -> Any:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    if not m:
        raise LLMError(f"DummyLLM: <{tag}> 블록 없음")
    try:
        return json.loads(m.group(1))
    except ValueError:
        raise LLMError(f"DummyLLM: <{tag}> JSON 파싱 실패") from None


def _p(v: float | None) -> str:
    return "-" if v is None else f"{v:+.2f}%"


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _md(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}월 {d.day}일"


IMAGE_PROMPT = ("Iron Nuna, a confident female news anchor in sleek red-and-gold armor, "
                "standing at a modern TV news studio desk, gesturing toward a blank screen, "
                "cinematic lighting, no text")
CLIP_PROMPT = ("Iron Nuna in a TV news studio, subtle head turn and gentle hand gesture, slow camera push-in, "
               "medium shot, no talking close-up, no text")


class DummyLLM:
    name = "dummy"

    def __init__(self) -> None:
        log.info("[NunaWeekly:llm] v%s 시작 · DummyLLM", VERSION)

    def _segment(self, seg: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
        limit = int(seg["chars_limit"])
        card = seg["card"]
        days = facts.get("days") or []
        title, lines = "", []
        if card == "none":
            narr = "아이언 누나입니다. 미국증시 요약입니다."
        elif card == "week":
            sp, nq = facts.get("week_sp500_chg"), facts.get("week_nasdaq_chg")
            if sp is not None and nq is not None:
                narr = f"이번 주 S&P500 {_p(sp)}, 나스닥 {_p(nq)} 누적입니다."
                lines = [f"S&P500 누적 {_p(sp)}", f"나스닥 누적 {_p(nq)}"]
            else:
                narr = "이번 주 월요일부터 수요일까지 흐름입니다."
                lines = ["월~수 흐름 정리"]
            title = "이번 주 요약"
        elif card == "day":
            day = days[int(seg["day_index"])]
            wd = WEEKDAY_KO[int(seg["day_index"])]
            title = f"{_md(day['us_date'])} {wd}"
            if day.get("closed"):
                narr = f"{wd}은 미국 증시 휴장이었습니다."
                lines = ["휴장"]
            else:
                narr = (f"{wd} S&P500 {_p(day['sp500_chg'])}, 나스닥 {_p(day['nasdaq_chg'])}, "
                        f"VIX {day['vix']:.2f}입니다.")
                lines = [f"S&P500 {_p(day['sp500_chg'])}", f"나스닥 {_p(day['nasdaq_chg'])}",
                         f"VIX {day['vix']:.2f}"]
        elif card == "keywords":
            narr = "이번 주 키워드는 주요 뉴스 흐름입니다."
            title, lines = "이번 주 키워드", ["헤드라인 점검", "변동성 확인"]
        elif card == "risk":
            fg = facts.get("fear_greed")
            narr = (f"공포탐욕지수는 {fg}입니다. 변동성에 유의하세요." if fg is not None
                    else "심리 지표를 점검하며 변동성에 유의하세요.")
            title = "리스크 점검"
            lines = [f"공포탐욕 {fg}"] if fg is not None else ["심리 지표 점검"]
        else:  # disclaimer
            narr = "시청 감사합니다. " + config.DISCLAIMER
        return {"seg_id": seg["seg_id"], "narration": _fit(narr, limit) if card != "disclaimer" else narr,
                "card_title": title, "card_lines": lines,
                "image_prompt": IMAGE_PROMPT if seg["visual"] in ("image", "image_card") else "",
                "clip_prompt": CLIP_PROMPT if seg["visual"] == "clip" else ""}

    def complete_json(self, system: str, user: str, max_tokens: int) -> LLMResult:
        facts = _between(user, "facts")
        segs = _between(user, "segments")
        title = "아이언 누나 주간 미국증시 브리핑"
        caption = f"{title}\n월요일부터 수요일까지 미국증시를 정리했습니다.\n\n{config.DISCLAIMER}"
        data = {"title": title, "hashtags": ["#미국증시", "#나스닥", "#아이언누나"],
                "captions": {p: caption for p in config.PLATFORMS},
                "segments": [self._segment(s, facts) for s in segs]}
        out = json.dumps(data, ensure_ascii=False)
        return LLMResult(data=data, input_tokens=(len(system) + len(user)) // 2, output_tokens=len(out) // 2,
                         model="dummy", request_id=None)
