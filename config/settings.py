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
SYSTEM_VERSION = "v1.9.0"

# ─── 시장 임계값 (Market Thresholds) ────────────────────────
VIX_LOW_THRESHOLD = 20.0
VIX_HIGH_THRESHOLD = 30.0
US10Y_LOW_THRESHOLD = 3.5
US10Y_HIGH_THRESHOLD = 4.5
OIL_LOW_THRESHOLD = 70.0
OIL_HIGH_THRESHOLD = 90.0
DXY_HIGH_THRESHOLD = 104.0

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
}

# FRED 시리즈 ID
FRED_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
    "hy_spread": "BAMLH0A0HYM2",
    "yield_curve": "T10Y2Y",
    "real_gdp_growth": "GDPC1",
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
X_MAX_TWEET_LENGTH = 280
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
