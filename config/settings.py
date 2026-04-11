"""
Investment OS — Settings (v1.5.0)
모든 상수와 설정을 중앙 관리한다.
v1.5.0 변경: Reddit(유료화) 제거 → 다중 RSS 소스로 대체
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# .env 로드
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ─── API Keys ───────────────────────────────────────────
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET", "")

# ─── 운영 모드 ────────────────────────────────────────────
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ─── 경로 ─────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = DATA_DIR / "outputs"
PUBLISHED_DIR = DATA_DIR / "published"
PHASE1_DIR = OUTPUT_DIR / "phase1"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
PHASE1_DIR.mkdir(parents=True, exist_ok=True)

# ─── 파일 경로 ────────────────────────────────────────────
CORE_DATA_FILE = OUTPUT_DIR / "core_data.json"
VALIDATION_FILE = OUTPUT_DIR / "validation_result.json"
PUBLISH_PAYLOAD_FILE = OUTPUT_DIR / "publish_payload.json"
HISTORY_FILE = PUBLISHED_DIR / "history.json"

# ─── 시스템 상수 ──────────────────────────────────────────
SYSTEM_NAME = "Investment OS"
SYSTEM_VERSION = "v1.20.0"

# ─── 시장 임계값 (Market Thresholds) ────────────────────────
VIX_LOW_THRESHOLD = 20.0
VIX_HIGH_THRESHOLD = 30.0
US10Y_LOW_THRESHOLD = 3.5
US10Y_HIGH_THRESHOLD = 4.5
OIL_LOW_THRESHOLD = 70.0
OIL_HIGH_THRESHOLD = 90.0
DXY_HIGH_THRESHOLD = 104.0

# ─── Tier 1 확장 시그널 임계값 (2026-04-01 추가) ──────────
# Fear & Greed Index 임계값 (0~100, alternative.me 기준)
# 24 이하 = Extreme Fear / 75 이상 = Extreme Greed
FEAR_GREED_FEAR_THRESHOLD = 30       # 이 이하면 공포 구간
FEAR_GREED_GREED_THRESHOLD = 70      # 이 이상이면 탐욕 구간

# BTC 24h 등락률 임계값 (%)
# BTC가 위험자산 선행지표로 작동 — 급락 시 리스크 오프 신호
BTC_RISK_DROP_THRESHOLD = -5.0       # 이 이하 급락 → Risk 가중
BTC_RISK_SURGE_THRESHOLD = 8.0       # 이 이상 급등 → 과열 경고

# S&P500/NASDAQ 일간 등락률 임계값 (%)
EQUITY_STRONG_MOVE_THRESHOLD = 1.5   # 이 이상 움직임 = 강한 모멘텀
EQUITY_CRASH_THRESHOLD = -2.0        # 이 이하 = 급락 신호

# XLF(금융) / GLD(금) 상대강도 판단 기준 (일간 등락률 차이 %)
# XLF > GLD → Risk-On 선호 / GLD > XLF → 안전자산 선호
XLF_GLD_RISK_ON_THRESHOLD = 1.0      # XLF-GLD > 1% → 금융 안정
XLF_GLD_RISK_OFF_THRESHOLD = -1.0    # XLF-GLD < -1% → 안전자산 선호

# ─── Tier 2 확장 시그널 임계값 (2026-04-01 추가) ──────────
# Market Breadth: RSP(균등가중)/SPY(시총가중) 등락률 차이 (%)
# RSP > SPY → 광범위 참여 (건강한 랠리)
# RSP < SPY → 소수 대형주 주도 (취약한 랠리)
BREADTH_HEALTHY_THRESHOLD = 0.3      # RSP-SPY > 0.3% → 광범위 참여
BREADTH_NARROW_THRESHOLD = -0.5      # RSP-SPY < -0.5% → 소수 종목 집중

# Vol Term Structure: VIX/VIX3M 비율
# > 1.0 (백워데이션) → 단기 패닉, 구조적 위기 의심
# < 0.85 (정상 컨탱고) → 안정
VOL_TERM_BACKWARDATION = 1.0         # VIX > VIX3M → 단기 패닉
VOL_TERM_NORMAL = 0.85               # VIX/VIX3M < 0.85 → 안정

# 실업수당 청구 (ICSA) — 주간 데이터, 천 명 단위
ICSA_LOW_THRESHOLD = 220.0           # 220K 이하 → 노동시장 강함
ICSA_HIGH_THRESHOLD = 300.0          # 300K 이상 → 노동시장 악화 경고

# 기대 인플레이션 (5Y Breakeven, T5YIFR) — %
INFLATION_EXP_LOW = 2.0              # 2% 이하 → 디스인플레이션
INFLATION_EXP_HIGH = 2.8             # 2.8% 이상 → 인플레이션 우려

# EM Stress: EEM(신흥국 ETF) 등락률 (%)
EM_STRESS_THRESHOLD = -2.0           # EEM < -2% → 신흥국 스트레스

# ─── Tier 3 확장 시그널 임계값 (2026-04-01 추가) ──────────
# AI Momentum: SOXX(반도체 ETF) vs QQQ 상대강도 (%)
# SOXX > QQQ → AI/반도체 리더십 건재 → Growth 강화
# SOXX < QQQ → AI 모멘텀 둔화
AI_MOM_STRONG_THRESHOLD = 0.5        # SOXX-QQQ > 0.5% → AI 리더십
AI_MOM_WEAK_THRESHOLD = -1.0         # SOXX-QQQ < -1% → AI 둔화

# Nasdaq Relative: NASDAQ - SP500 등락률 차이 (%)
# NASDAQ >> SP500 → Growth/Tech 주도
# NASDAQ << SP500 → 방어주/가치주 선호
NASDAQ_REL_GROWTH_THRESHOLD = 0.5    # NASDAQ-SP500 > 0.5% → Growth 주도
NASDAQ_REL_VALUE_THRESHOLD = -0.5    # NASDAQ-SP500 < -0.5% → Value 선호

# Banking Stress: KRE(지역은행 ETF) vs XLF(금융 전체) 등락률 차이 (%)
# KRE << XLF → 지역은행 약세 = 금융 시스템 스트레스
# 2023 SVB 사태 시 KRE가 XLF 대비 대폭 하락한 패턴
BANK_STRESS_THRESHOLD = -1.5         # KRE-XLF < -1.5% → 은행 스트레스

# ─── Priority A 확장 시그널 임계값 (2026-04-11 추가) ──────
# [A-1] Gold 안전자산 시그널
GOLD_SAFE_HAVEN_STRONG = 1.5    # gold_change > 1.5% → 강한 안전자산 수요
GOLD_SAFE_HAVEN_MILD   = 0.5    # gold_change > 0.5% → 소폭 수요
GOLD_RISK_ON_THRESHOLD = -1.0   # gold_change < -1.0% → 리스크온 (금 매도)

# [A-2] Small Cap Relative Strength (IWM - SPY 등락률 차이, %p)
SMALL_CAP_RISK_ON_THR      =  1.5   # IWM - SPY > +1.5%p → 리스크ON
SMALL_CAP_RISK_OFF_THR     = -1.5   # IWM - SPY < -1.5%p → 리스크OFF
SMALL_CAP_RISK_OFF_EXTREME = -3.0   # IWM - SPY < -3.0%p → 극단 리스크OFF

# [A-3] MOVE Index (ICE 채권 변동성)
MOVE_CALM     = 80.0    # < 80  : 채권 시장 안정
MOVE_ELEVATED = 110.0   # 80~110: 긴장 경계
MOVE_STRESSED = 140.0   # > 140 : 채권 위기 → Alert L1

# [A-4] Stagflation Signal (SPY + TLT 동반 하락)
STAGFLATION_SPY_THR = -1.0   # SPY < -1.0% 동시 조건
STAGFLATION_TLT_THR = -1.0   # TLT < -1.0% 동시 조건 → L2 Alert
# [A-4] SPY/TLT 4상한 판정 dead zone (보합 구간 노이즈 제거)
# ±0.3% 이내 변동은 방향성 없는 노이즈로 처리 → Stagflation Fear 과민 방지
QUADRANT_FLAT_THRESHOLD = 0.3

# [A-5] Yield Spread (bp 기준, 10Y - 2Y)
YIELD_SPREAD_NORMAL_BP = 50.0    # > +50bp : 정상 스티프
YIELD_SPREAD_FLAT_BP   =  0.0    # 0 ~ +50 : 평탄화 경계
YIELD_SPREAD_DEEP_BP   = -50.0   # < -50bp : 심화 역전 → L1 Alert

# [A-6] SPY SMA 기간 설정
SPY_SMA_DAYS_SHORT  = 22    # SMA5/20용 (기존 1mo ≈ 22 영업일)
SPY_SMA_DAYS_LONG   = 270   # SMA50/200용 (1년치 여유분)

# ─── ETF Universe ─────────────────────────────────────────
ETF_CORE = ["QQQM", "XLK", "SPYM", "XLE", "ITA", "TLT"]
ETF_SIGNAL = ["XLF", "GLD"]
ETF_ALL = ETF_CORE + ETF_SIGNAL

TICKER_MAP = {
    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "VIX": "^VIX",
    "US10Y": "^TNX",
    "OIL": "CL=F",
    "DXY": "DX-Y.NYB",
    "QQQM": "QQQM",
    "XLK": "XLK",
    "SPYM": "SPYM",
    "XLE": "XLE",
    "ITA": "ITA",
    "TLT": "TLT",
    "XLF": "XLF",
    "GLD": "GLD",
    # ── Tier 2 확장 (2026-04-01 추가) ──
    "RSP":   "RSP",       # T2-1: Invesco S&P500 Equal Weight (Market Breadth)
    "VIX3M": "^VIX3M",    # T2-2: CBOE 3-Month Volatility (Vol Term Structure)
    "EEM":   "EEM",        # T2-5: iShares MSCI Emerging Markets (EM Stress)
    # ── Tier 3 확장 (2026-04-01 추가) ──
    "SOXX":  "SOXX",       # T3-1: iShares Semiconductor (AI Momentum)
    "KRE":   "KRE",        # T3-3: SPDR Regional Banking (Banking Stress)
    # ── Priority A 확장 (2026-04-11 추가) ──────────────────
    "IWM":  "IWM",      # Russell 2000 ETF
    "GOLD": "GC=F",     # Gold Futures (현물 대리)
    # MOVE: ^MOVE는 직접 fetch (수집 불안정으로 TICKER_MAP 제외)
}

# FRED 시리즈 ID
FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "hy_spread": "BAMLH0A0HYM2",
    "yield_curve": "T10Y2Y",
    "real_gdp_growth": "GDPC1",
    # ── Tier 2 확장 (2026-04-01 추가) ──
    "initial_claims": "ICSA",        # T2-3: 주간 신규 실업수당 청구건수 (천 명)
    "inflation_exp":  "T5YIFR",      # T2-4: 5년 기대 인플레이션율 (%)
    # ── Priority A 확장 (2026-04-11 추가) ──────────────────
    "us2y": "DGS2",     # 2-Year Treasury Constant Maturity Rate
}

# ─── 다중 RSS 소스 설정 (Reddit 대체) ──────────────────────
# weight: 감성 집계 시 가중치 (높을수록 영향력 큼)
# max_items: 소스당 최대 수집 헤드라인 수
# timeout_sec: 소스별 fetch 타임아웃
RSS_SOURCES = [
    {
        "name": "Google News — Market",
        "url": "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en",
        "weight": 1.0,
        "max_items": 10,
        "timeout_sec": 8,
    },
    {
        "name": "Google News — Fed",
        "url": "https://news.google.com/rss/search?q=Federal+Reserve+interest+rates&hl=en-US&gl=US&ceid=US:en",
        "weight": 1.2,
        "max_items": 8,
        "timeout_sec": 8,
    },
    {
        "name": "Google News — ETF",
        "url": "https://news.google.com/rss/search?q=S%26P+500+ETF+investing&hl=en-US&gl=US&ceid=US:en",
        "weight": 1.0,
        "max_items": 8,
        "timeout_sec": 8,
    },
    {
        "name": "Yahoo Finance — Markets",
        "url": "https://finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
        "weight": 1.3,
        "max_items": 10,
        "timeout_sec": 8,
    },
    {
        "name": "Yahoo Finance — Top Stories",
        "url": "https://finance.yahoo.com/news/rssindex",
        "weight": 1.1,
        "max_items": 10,
        "timeout_sec": 8,
    },
    {
        "name": "CNBC — Markets",
        "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "weight": 1.5,
        "max_items": 10,
        "timeout_sec": 10,
    },
    {
        "name": "CNBC — Economy",
        "url": "https://www.cnbc.com/id/19832390/device/rss/rss.html",
        "weight": 1.4,
        "max_items": 8,
        "timeout_sec": 10,
    },
    {
        "name": "MarketWatch — Top Stories",
        "url": "https://feeds.marketwatch.com/marketwatch/topstories/",
        "weight": 1.2,
        "max_items": 10,
        "timeout_sec": 10,
    },
    {
        "name": "Investing.com — Stock Market",
        "url": "https://www.investing.com/rss/news_25.rss",
        "weight": 1.1,
        "max_items": 8,
        "timeout_sec": 10,
    },
]

# RSS 요청 공통 User-Agent (미설정 시 일부 소스 차단)
RSS_USER_AGENT = (
    "Mozilla/5.0 (compatible; InvestmentOS/1.5.0; +https://github.com/investment-os)"
)

# 감성 판단 임계값
# net_weighted_score >= BULLISH_THRESHOLD → Bullish
# net_weighted_score <= BEARISH_THRESHOLD → Bearish
SENTIMENT_BULLISH_THRESHOLD = 0.5
SENTIMENT_BEARISH_THRESHOLD = -0.5

# ─── 스케줄 (KST 기준) ────────────────────────────────────
SCHEDULE_MORNING = "06:30"
SCHEDULE_FULL    = "18:30"  # v1.8.0 신규: 풀버전 대시보드 (KST)
SCHEDULE_INTRADAY = "23:30"
SCHEDULE_CLOSE = "07:00"

# ─── X 발행 설정 ──────────────────────────────────────────
# Short-form 한도 (알림/스냅샷/주간쓰레드 등 의도적으로 짧게 유지하는 포맷)
X_MAX_TWEET_LENGTH = 280

# Premium 계정 장문 한도 (AI 내러티브 등 long-form 콘텐츠용)
# X Premium / Premium+ : 최대 25,000자
X_PREMIUM_TWEET_LENGTH = 25000

X_HASHTAGS = "#ETF #투자 #미국증시"

# ─── 중복 검사 ────────────────────────────────────────────
DUPLICATE_CHECK_COUNT = 10

# ─── 이미지 생성 설정 ─────────────────────────────────────
IMAGES_DIR = DATA_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# 이미지 크기 (X 권장 정사각형)
IMAGE_WIDTH  = 1080
IMAGE_HEIGHT = 1080
IMAGE_DPI    = 96
IMAGE_FORMAT = "PNG"

# 대시보드 코드네임 (푸터 표시)
CODENAME = "EDT Investment"

# ── Telegram ────────────────────────────────────────────
TELEGRAM_FREE_CHANNEL  = "free"   # 무료 채널 식별자
TELEGRAM_PAID_CHANNEL  = "paid"   # 유료 채널 식별자

# 세션별 영문 표시명
SESSION_LABELS = {
    "morning":  "Morning Brief",
    "intraday": "Intraday Briefing",
    "close":    "Close Summary",
    "weekly":   "Weekly Review",
    "postmarket": "Market Snapshot",
}

# 컬러 팔레트 (다크 테마)
COLORS = {
    "bg":          "#0d1117",   # 전체 배경
    "card":        "#161b22",   # 카드 배경
    "border":      "#2d3748",   # 구분선
    "text":        "#e2e8f0",   # 기본 텍스트
    "text_sub":    "#8892a4",   # 서브 텍스트
    "red":         "#ef4444",   # 하락/위험/Underweight
    "green":       "#22c55e",   # 상승/안전/Overweight
    "yellow":      "#f59e0b",   # 주의/MEDIUM
    "purple":      "#8b5cf6",   # Signal/HEDGE
    "blue":        "#3b82f6",   # 강조
    "orange":      "#f97316",   # OilShock/Warning
    "regime": {
        "Risk-On":          "#059669",
        "Risk-Off":         "#dc2626",
        "Oil Shock":        "#d97706",
        "Liquidity Crisis": "#7c3aed",
        "Recession Risk":   "#9f1239",
        "Stagflation Risk": "#b45309",
        "AI Bubble":        "#0369a1",
        "Transition":       "#4b5563",
        "Crisis Regime":    "#dc2626",
    },
}
