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

# ─── Priority B 확장 시그널 임계값 (2026-04-11 추가) ──────
# [B-1] CPI YoY 기준 (%)
CPI_HOT       = 3.5    # YoY > 3.5% → Hot (연준 긴축 압박)
CPI_ELEVATED  = 2.5    # YoY > 2.5% → Elevated
CPI_COOL      = 1.5    # YoY < 1.5% → Cool (디스인플레이션)

# [B-2] NFP MoM 기준 (천 명)
NFP_STRONG    = 200    # +200K 이상 → 강한 고용
NFP_MODERATE  = 50     # +50K 이상  → 보통
NFP_WEAK      = 0      # 0 미만      → 고용 감소 (위험)

# [B-3] 섹터 로테이션 기준 (방어 vs 경기민감 등락률 차이 %p)
SECTOR_DEFENSIVE_THR = 1.0    # 방어주 > 경기민감 1%p 이상 → 방어 로테이션
SECTOR_CYCLICAL_THR  = 1.0    # 경기민감 > 방어주 1%p 이상 → 공격 로테이션

# [B-4] Copper/Gold Ratio 변화율 기준 (5일 변화율 %)
COPPER_GOLD_OPTIMISM  =  2.0  # +2% 이상 → 경기 낙관
COPPER_GOLD_PESSIMISM = -2.0  # -2% 이하 → 경기 비관

# [B-7] 연준 자산 주간 변화 기준 (10억 달러 단위)
FED_BS_QE_THR = 30.0   # +300억 이상 → QE Active (유동성 확장)
FED_BS_QT_THR = -30.0  # -300억 이하 → QT Active (유동성 축소)

# [B-8] SOFR 스트레스 기준 (SOFR - Fed Funds Rate 차이 %p)
SOFR_STRESS_THR  = 0.5   # 차이 > 0.5%p → 단기자금 스트레스
SOFR_TENSION_THR = 0.2   # 차이 > 0.2%p → 긴장 경계

# [A-4 고도화] TLT 4단계 건강도 임계값 (v1.9.0 추가)
TLT_RALLY_THR  =  0.5   # ≥ +0.5%  → Rally  (채권 강세)
TLT_STABLE_THR = -0.5   # > -0.5%  → Stable (보합)
TLT_WEAK_THR   = -1.5   # > -1.5%  → Weak   (채권 약세)
                         # ≤ -1.5%  → Crash  (채권 급락)
US30Y_HIGH_THR =  5.0   # > 5.0%   → 고금리 압박 (score +1 보정)
US30Y_LOW_THR  =  3.5   # < 3.5%   → 저금리 지지 (score -1 보정)

# ─── 아이템 4: TIPS Breakeven Inflation 임계값 (2026-04-29 추가) ──
BEI_HOT          = 2.8   # > 2.8% → 인플레 기대 과열
BEI_ELEVATED     = 2.3   # 2.3~2.8% → 주의 구간
BEI_LOW          = 1.8   # < 1.8% → 디플레 우려
REAL_RATE_TIGHT  = 2.0   # 실질금리 > 2.0% → 성장주 밸류에이션 압박
REAL_RATE_EASY   = 0.0   # 실질금리 < 0.0% → 마이너스 실질금리 → 성장주 지지

# ─── 아이템 5: IG Credit Spread 임계값 (2026-04-29 추가) ──────────
IG_SPREAD_LOW      = 1.0   # < 1.0% → 크레딧 양호
IG_SPREAD_ELEVATED = 1.5   # 1.0~1.5% → 주의 구간
IG_SPREAD_STRESS   = 2.5   # > 2.5% → 크레딧 스트레스

# ─── 아이템 6: US30Y Spread 임계값 (2026-04-29 추가) ─────────────
SPREAD_2Y30Y_STEEP  = 150.0  # > 150bp → 장기 성장 기대 강함
SPREAD_2Y30Y_NORMAL =  50.0  # 50~150bp → 정상 범위
SPREAD_2Y30Y_FLAT   =   0.0  # 0~50bp → 장기 우려 / < 0bp → 역전

# ─── 아이템 7: MOVE VIX 괴리 감지 임계값 (2026-04-29 추가) ──────
# 기존: MOVE_CALM=80, MOVE_ELEVATED=110, MOVE_STRESSED=140 유지
MOVE_VIX_DIVERGE_MOVE_THR = 110.0  # MOVE >= 110 (Elevated 이상)
MOVE_VIX_DIVERGE_VIX_THR  =  20.0  # AND VIX <= 20 → 채권 선행 스트레스

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
    # ── Priority B 확장 (2026-04-11 추가) ──────────────────
    "COPPER": "HG=F",   # B-4: 구리 선물 (닥터 코퍼)
    "XLV":  "XLV",      # B-3: 헬스케어
    "XLU":  "XLU",      # B-3: 유틸리티
    "XLI":  "XLI",      # B-3: 산업재
    "XLP":  "XLP",      # B-3: 필수소비재
    "XLRE": "XLRE",     # B-3: 리츠
    "XLB":  "XLB",      # B-3: 소재
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
    # ── Priority B 확장 (2026-04-11 추가) ──────────────────
    "cpi":               "CPIAUCSL",   # B-1: 소비자물가지수
    "core_cpi":          "CPILFESL",   # B-1: 근원 CPI (식품·에너지 제외)
    "nfp":               "PAYEMS",     # B-2: 비농업부문 고용 (천 명)
    "unemployment":      "UNRATE",     # B-2: 실업률 (%)
    "fed_balance_sheet": "WALCL",      # B-7: 연준 총자산 (백만 달러)
    "sofr":              "SOFR",       # B-8: SOFR (담보부 익일물 금리)
    # ── 신규 추가 (2026-04-28) ──────────────────────────────
    "us10y": "DGS10",   # 10-Year Treasury Constant Maturity Rate (절대값)
    "us30y": "DGS30",   # 30-Year Treasury Constant Maturity Rate (절대값)
  
    # ── 아이템 4/5 추가 (2026-04-29) ─────────────────────────────
    "bei_5y":    "T5YIE",       # 5Y Breakeven Inflation Rate
    "bei_10y":   "T10YIE",      # 10Y Breakeven Inflation Rate
    "ig_spread": "BAMLC0A0CM",  # ICE BofA US Corporate OAS (IG Spread)
}
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
     # ── 신규 추가 (2026-04-28) ──────────────────────────────
    {
        "name": "Fox Business — Markets",
        "url": "https://moxie.foxbusiness.com/google-publisher/markets.xml",
        "weight": 1.2,
        "max_items": 10,
        "timeout_sec": 8,
    },
    {
        "name": "Fox Business — Economy",
        "url": "https://moxie.foxbusiness.com/google-publisher/economy.xml",
        "weight": 1.2,
        "max_items": 8,
        "timeout_sec": 8,
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
