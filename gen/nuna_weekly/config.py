"""
gen/nuna_weekly/config.py — 상수·환경변수 단일 해석 지점
=========================================================
값의 출처는 설계서(2026-10-19)와 마스터 결정이다. 확정 전 항목(C1·D1·S-04)은
하드코딩하지 않고 환경변수 스위치로 둔다 — 기본값은 아래 주석 참조.

비밀값은 여기서 읽지 않는다 (secret_env.py 담당).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "1.1.0"

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")

PKG_DIR = Path(__file__).resolve().parent
GEN_DIR = PKG_DIR.parent
OUT_ROOT = GEN_DIR / "data" / "nuna_out"          # 회차별 하위 폴더
ASSETS_DIR = PKG_DIR / "assets"
CANON_FILE = PKG_DIR / "canon_nuna.json"


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v.strip() if isinstance(v, str) and v.strip() else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


# ── 실행 스위치 ─────────────────────────────────────────────────────
def dry_run() -> bool:
    """기존 secret DRY_RUN 규약(main.yml:188) — 'false' 일 때만 실발행. 기본 true."""
    return _env("DRY_RUN", "true").lower() != "false"


def enabled() -> bool:
    """vars NUNA_ENABLED — schedule 실행은 '1' 일 때만 진행한다."""
    return _env("NUNA_ENABLED", "0") == "1"


def force_run() -> bool:
    """기존 규약(main.yml:189) — 달력 게이트 무시."""
    return _env("FORCE_RUN", "false").lower() in ("true", "1", "yes")


# ── 확정 대기 스위치 (설계서 16장 끝) ────────────────────────────────
def duration_sec() -> int:
    """C1 — 기본 60초(전 채널 단일본). 70 이면 '이번 주 키워드' 구간 포함."""
    d = _env_int("NUNA_DURATION_SEC", 60)
    if d not in (60, 70):
        raise ValueError(f"NUNA_DURATION_SEC 는 60 또는 70 만 허용: {d}")
    return d


def fb_cut_enabled() -> bool:
    """C1 ②안 — 본편 70초일 때 Facebook 60초 편집본 별도 생성. 기본 off."""
    return _env("NUNA_FB_CUT", "0") == "1"


def market_source() -> str:
    """D1 — 'direct'(실행 시점 Yahoo 직접 수집, 기본) | 'supabase'(daily_snapshots)."""
    s = _env("NUNA_MARKET_SOURCE", "direct").lower()
    if s not in ("direct", "supabase"):
        raise ValueError(f"NUNA_MARKET_SOURCE 허용값 아님: {s}")
    return s


# ── 예산 (Q4) ───────────────────────────────────────────────────────
def budget_episode_krw() -> int:
    return _env_int("NUNA_BUDGET_EPISODE_KRW", 2000)


def budget_month_krw() -> int:
    return _env_int("NUNA_BUDGET_MONTH_KRW", 15000)


def fx_fallback() -> float:
    """daily_snapshots.usdkrw 미확보 시 보수적 환율."""
    return _env_float("NUNA_FX_FALLBACK", 1500.0)


# ── 모델·엔드포인트 (설계서 6·8장) ───────────────────────────────────
def script_model() -> str:
    return _env("NUNA_SCRIPT_MODEL", "claude-sonnet-5-5")


def tts_model() -> str:
    return _env("NUNA_TTS_MODEL", "gemini-3.8-flash-tts")


def tts_voice() -> str:
    """PoC P3 에서 마스터가 선택 — 확정 전 기본값 Kore (문서 표기 'Firm')."""
    return _env("NUNA_TTS_VOICE", "Kore")


TTS_STYLE = "차분하고 신뢰감 있는 한국어 경제 뉴스 앵커"

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_IMAGE_ENDPOINT = "fal-ai/nano-banana/edit"
FAL_CLIP_ENDPOINT = "fal-ai/kling-video/v2.6/pro/image-to-video"

# 단가 (USD) — 출처: 모델 페이지 2026-10-19 확인
PRICE_IMAGE_USD = 0.039              # nano-banana/edit, 장당
PRICE_CLIP_USD_PER_SEC = 0.07        # kling 2.6 pro i2v, generate_audio=false
PRICE_CLAUDE_IN_PER_MTOK = 2.0       # claude-sonnet-5-5
PRICE_CLAUDE_OUT_PER_MTOK = 10.0
PRICE_TTS_OUT_PER_MTOK = 9.0         # gemini-3.8-flash-tts (2026-12-31 까지)
PRICE_TTS_IN_PER_MTOK = 0.5
TTS_TOKENS_PER_SEC = 25

CLIP_SECONDS = 5

# ── fal 대기 상한 (W44 clips 300s 타임아웃 대응, 2026-10-30) ─────────
# 이미지(nano-banana)는 실측 ~10s, 클립(kling i2v)은 300s 경과 시 IN_PROGRESS 였다.
# 단일 상한을 쓰지 않고 작업 종류별로 분리한다. 워크플로 timeout-minutes 안에 들어야 한다.
FAL_WAIT_MIN_SEC = 60
FAL_WAIT_MAX_SEC = 1200
# 회차당 fal 대기 상한 합계 — nuna_weekly_gen.yml timeout-minutes(90) 에서 설치·대본·TTS·조립 여유를 뺀 값
FAL_WAIT_TOTAL_MAX_SEC = 70 * 60


def _fal_wait(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        v = int(raw)
    except ValueError:
        raise ValueError(f"{name} 는 정수 초만 허용: {raw!r}") from None
    if not FAL_WAIT_MIN_SEC <= v <= FAL_WAIT_MAX_SEC:
        raise ValueError(f"{name} 는 {FAL_WAIT_MIN_SEC}~{FAL_WAIT_MAX_SEC} 초만 허용: {v}")
    return v


def fal_image_max_wait_sec() -> int:
    return _fal_wait("NUNA_FAL_IMAGE_MAX_WAIT_SEC", 300)


def fal_clip_max_wait_sec() -> int:
    return _fal_wait("NUNA_FAL_CLIP_MAX_WAIT_SEC", 900)

# ── 스케줄 (Q7, C2) ─────────────────────────────────────────────────
PUBLISH_HOUR_KST = 8
PUBLISH_MINUTE_KST = 30
HOLD_WINDOW_MIN = 60
COLLECT_WAIT_MAX_SEC = 20 * 60       # supabase 모드에서만 사용

# ── 영상 규격 (Reels 1080×1920·30fps) ────────────────────────────────
VIDEO_W, VIDEO_H, VIDEO_FPS = 1080, 1920, 30


@dataclass(frozen=True)
class Segment:
    seg_id: str
    start: float
    end: float
    visual: str          # clip | image | image_card | card_only
    card: str            # none | breaking | week | chart | issue | day | keywords | risk | disclaimer
    day_index: int = -1  # (구) day 카드일 때 0=월,1=화,2=수 — DN2026_0086 이후 시트에서는 미사용

    @property
    def seconds(self) -> float:
        return round(self.end - self.start, 3)


# 70초 컷 시트. 60초 = S6(키워드) 제외 후 뒤 구간을 10초 당긴다.
# DN2026_0086 뉴스 방송 큐시트: S1 이번 주 핵심(훅) · S2 주간 성적 · S3 월~금 그래프 · S4·S5 원인 해설 ·
#   S7 리스크·다음 주 관전 포인트 · S8 마무리·면책. 구간 ID·visual·길이는 그대로(이미지·클립 자산 계약 유지).
_SHEET_70: tuple[Segment, ...] = (
    Segment("S1", 0, 5, "clip", "breaking"),
    Segment("S2", 5, 13, "image_card", "week"),
    Segment("S3", 13, 22, "image_card", "chart"),
    Segment("S4", 22, 31, "image_card", "issue"),
    Segment("S5", 31, 40, "image_card", "issue"),
    Segment("S6", 40, 50, "image_card", "keywords"),
    Segment("S7", 50, 62, "image_card", "risk"),
    Segment("S8", 62, 70, "clip", "disclaimer"),
)


def segments(total: int | None = None) -> list[Segment]:
    """total 초(60|70)에 맞는 구간 목록. S8 은 클립 5초 + 엔드카드 3초."""
    total = total or duration_sec()
    if total == 70:
        return list(_SHEET_70)
    out: list[Segment] = []
    shift = 0.0
    for s in _SHEET_70:
        if s.seg_id == "S6":
            shift = s.seconds
            continue
        out.append(Segment(s.seg_id, s.start - shift, s.end - shift, s.visual, s.card, s.day_index))
    return out


def chars_limit(seconds: float) -> int:
    """구간 내레이션 글자수 상한 — hero voiceover.py:93 휴리스틱(6.5자/초, 0.8초 여유)."""
    return max(10, int((seconds - 0.8) * 6.5))


CHARS_MIN_RATIO = 0.70   # DN2026_0086: 무음 축소(W44 무음 16.0초/60초 실측). R6-05: 0.85 는 허용 폭이 좁아 재생성 실패 위험


def chars_min(seconds: float) -> int:
    """구간 내레이션 글자수 하한 — 상한의 70%."""
    return int(chars_limit(seconds) * CHARS_MIN_RATIO)


# ── 방송 그래픽 (DN2026_0086) ──────────────────────────────────────
NEWS_BRAND = "EDT 마켓 뉴스"        # 가칭(회장 승인 2026-10-31)
NEWS_BADGE = "주간"                  # 생방송·속보가 아니므로 LIVE·속보 표기를 쓰지 않는다


# ── 채널 (Q9) ───────────────────────────────────────────────────────
PLATFORMS: tuple[str, ...] = ("instagram", "threads", "youtube", "facebook")
KEY_ALIAS: dict[str, str] = {"instagram": "main", "threads": "main",
                             "youtube": "sub", "facebook": "sub"}
ZERNIO_KEY_ENV: dict[str, str] = {"main": "ZERNIO_API_KEY", "sub": "ZERNIO_API_SUB_KEY"}
ACCOUNT_ENV: dict[str, str] = {
    "instagram": "NUNA_INSTAGRAM_ACCOUNT_ID",
    "threads": "NUNA_THREADS_ACCOUNT_ID",
    "youtube": "NUNA_YOUTUBE_ACCOUNT_ID",
    "facebook": "NUNA_FACEBOOK_ACCOUNT_ID",
}
CAPTION_LIMITS: dict[str, int] = {"instagram": 2200, "threads": 500,
                                  "youtube": 5000, "facebook": 2200}
YOUTUBE_TITLE_LIMIT = 100
THREADS_TOPIC_TAG = "미국증시"
IG_MAX_BYTES = 300 * 1024 * 1024
TELEGRAM_VIDEO_MAX_BYTES = 50 * 1024 * 1024

DISCLAIMER = "투자 참고용 정보이며 투자 권유가 아닙니다"

# ── 시장 지표 (Yahoo v8 chart, 기존 config/settings.py TICKER_MAP 과 동일 심볼) ──
TICKERS: dict[str, str] = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "vix": "^VIX",
    "us10y": "^TNX",
    "wti": "CL=F",
    "dxy": "DX-Y.NYB",
}


def out_dir(episode_id: str) -> Path:
    p = OUT_ROOT / episode_id
    p.mkdir(parents=True, exist_ok=True)
    return p
