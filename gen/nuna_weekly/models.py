"""
gen/nuna_weekly/models.py — 단계 간 데이터 계약 (dataclass)
===========================================================
모든 단계는 이 구조로만 데이터를 주고받는다. JSON 직렬화는 to_dict / from_dict.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

VERSION = "1.0.0"

# 원장 상태 (DB CHECK 제약과 동일 — db/migrations nuna_001_tables)
STATUSES = ("PLANNED", "SKIPPED", "COLLECTED", "SCRIPTED", "VOICED", "IMAGED", "CLIPPED",
            "ASSEMBLED", "QC_PASSED", "STAGED", "NOTIFIED", "HELD", "SCHEDULED",
            "PUBLISHED", "PARTIAL", "FAILED")
GEN_ORDER = ("PLANNED", "COLLECTED", "SCRIPTED", "VOICED", "IMAGED", "CLIPPED",
             "ASSEMBLED", "QC_PASSED", "STAGED", "NOTIFIED")


@dataclass
class GateResult:
    ok: bool
    episode_id: str
    publish_date: date
    us_days: list[date]                 # 휴장 제외 실제 거래일
    holidays: dict[str, str]            # {"2026-11-26": "Thanksgiving Day"}
    skip_reason: str | None = None


@dataclass
class DayFacts:
    us_date: str                        # YYYY-MM-DD (미국 거래일)
    closed: bool = False
    holiday_name: str = ""
    # 지표: 종가와 전일 대비 변화율(%) — direct 수집 시 둘 다, supabase 모드는 change 만
    sp500_close: float | None = None
    sp500_chg: float | None = None
    nasdaq_close: float | None = None
    nasdaq_chg: float | None = None
    vix: float | None = None
    vix_chg: float | None = None
    us10y: float | None = None       # ^TNX 값(%)
    wti: float | None = None
    dxy: float | None = None


@dataclass
class Facts:
    episode_id: str
    source: str                         # direct | supabase
    fetched_at: str                     # ISO8601 KST
    days: list[DayFacts]
    week_sp500_chg: float | None = None   # 첫 거래일 전일종가 → 마지막 거래일 종가 누적(%)
    week_nasdaq_chg: float | None = None
    regime: str | None = None
    risk_level: str | None = None
    regime_date: str | None = None   # daily_analysis 행 날짜(KST)
    fear_greed: int | None = None
    fear_greed_label: str | None = None
    headlines: list[dict[str, str]] = field(default_factory=list)  # {"date","text"}
    top_issues: list[str] = field(default_factory=list)
    usdkrw: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Facts:
        d = dict(d)
        d["days"] = [DayFacts(**x) for x in d.get("days", [])]
        return cls(**d)


@dataclass
class ScriptSegment:
    seg_id: str
    target_sec: float
    narration: str
    card_title: str = ""
    card_lines: list[str] = field(default_factory=list)
    image_prompt: str = ""              # 이미지 구간용 장면 묘사(영문)
    clip_prompt: str = ""               # 클립 구간용 동작 묘사(영문)


@dataclass
class Script:
    episode_id: str
    title: str                          # YouTube 제목(≤100자)
    segments: list[ScriptSegment]
    captions: dict[str, str]            # platform → 캡션
    hashtags: list[str] = field(default_factory=list)
    model: str = ""
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Script:
        d = dict(d)
        d["segments"] = [ScriptSegment(**s) for s in d.get("segments", [])]
        return cls(**d)


@dataclass
class Asset:
    kind: str                           # image | clip | audio
    key: str                            # 예: img_S2, clip_S1, tts_S3
    local_path: str
    remote_url: str | None = None    # fal 결과 URL (재다운로드용, 로그 출력 금지)
    request_id: str | None = None
    seconds: float | None = None
    sha256: str | None = None


@dataclass
class QCReport:
    variant: str
    passed: bool
    gates: dict[str, dict[str, Any]]    # gate_id → {"ok": bool, "detail": ...}
    measured: dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetResult:
    platform: str
    status: str                         # pending|scheduled|published|failed|blocked|skipped
    zernio_post_id: str | None = None
    platform_post_url: str | None = None
    error: str | None = None
