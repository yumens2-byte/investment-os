"""
collectors/crypto_com_client.py (v1.0.0)
=========================================
Phase 1A — Crypto.com REST API 클라이언트

용도:
  - T4-1 Crypto Basis Spread 시그널 수집
  - BTC Mark Price vs Index Price 차이 계산

API:
  - Base URL: https://api.crypto.com/exchange/v1/public
  - 인증: 불필요 (public endpoints만 사용)
  - Rate limit: 사실상 무제한 (공식 명시 없음)

엔드포인트:
  1. /public/get-mark-price?instrument_name=BTCUSD-PERP
  2. /public/get-index-price?instrument_name=BTCUSD-INDEX

시그널 판정:
  basis_spread = (mark - index) / index * 100
  > 1.0    → Premium  (score=3, 선물 과열)
  -1.0~1.0 → Normal   (score=2, 정상)
  < -1.0   → Discount (score=1, 바닥 신호)
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"

# Crypto.com Exchange API v1
_BASE_URL = "https://api.crypto.com/exchange/v1/public"
_TIMEOUT_SEC = 5
_RETRY_COUNT = 3
_RETRY_BACKOFF = [1, 2, 4]  # 지수 백오프

# 기본 심볼
DEFAULT_MARK_SYMBOL = "BTCUSD-PERP"
DEFAULT_INDEX_SYMBOL = "BTCUSD-INDEX"


def _http_get(endpoint: str, params: dict) -> Optional[dict]:
    """
    Crypto.com public API 호출 (재시도 포함).

    Args:
        endpoint: API path (예: "/public/get-mark-price")
        params: query params

    Returns:
        dict: {"code": 0, "result": {...}} 또는 None
    """
    import requests

    url = f"{_BASE_URL}{endpoint}"
    headers = {
        "User-Agent": "InvestmentOS/1.0 (+https://github.com/yumens2-byte/investment-os)",
        "Accept": "application/json",
    }

    last_error = None
    for attempt in range(_RETRY_COUNT):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT_SEC)

            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    f"[CryptoCom] {endpoint} HTTP {resp.status_code} "
                    f"(attempt {attempt + 1}/{_RETRY_COUNT})"
                )
                if attempt < _RETRY_COUNT - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
                    continue
                return None

            data = resp.json()
            code = data.get("code")
            if code != 0:
                last_error = f"code={code}"
                logger.warning(f"[CryptoCom] {endpoint} 오류 코드: {code}")
                return None

            return data

        except requests.Timeout:
            last_error = "timeout"
            logger.warning(
                f"[CryptoCom] {endpoint} 타임아웃 "
                f"(attempt {attempt + 1}/{_RETRY_COUNT})"
            )
            if attempt < _RETRY_COUNT - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            return None

        except Exception as e:
            last_error = str(e)
            logger.warning(f"[CryptoCom] {endpoint} 예외: {e}")
            return None

    logger.error(f"[CryptoCom] {endpoint} 최종 실패: {last_error}")
    return None


def get_mark_price(instrument: str = DEFAULT_MARK_SYMBOL) -> Optional[float]:
    """
    선물 마크 가격 조회.

    Args:
        instrument: 상품명 (예: BTCUSD-PERP)

    Returns:
        float: 마크 가격
        None: 실패
    """
    data = _http_get(
        "/public/get-mark-price",
        {"instrument_name": instrument},
    )
    if not data:
        return None

    try:
        result = data.get("result", {})
        items = result.get("data", [])
        if not items:
            logger.warning(f"[CryptoCom] mark_price 데이터 없음: {instrument}")
            return None

        price_str = items[0].get("v")
        if price_str is None:
            return None

        price = float(price_str)
        logger.debug(f"[CryptoCom] mark_price {instrument} = {price}")
        return price

    except (KeyError, ValueError, IndexError) as e:
        logger.warning(f"[CryptoCom] mark_price 파싱 실패: {e}")
        return None


def get_index_price(instrument: str = DEFAULT_INDEX_SYMBOL) -> Optional[float]:
    """
    인덱스 가격 조회.

    Args:
        instrument: 상품명 (예: BTCUSD-INDEX)

    Returns:
        float: 인덱스 가격
        None: 실패
    """
    data = _http_get(
        "/public/get-index-price",
        {"instrument_name": instrument},
    )
    if not data:
        return None

    try:
        result = data.get("result", {})
        items = result.get("data", [])
        if not items:
            logger.warning(f"[CryptoCom] index_price 데이터 없음: {instrument}")
            return None

        price_str = items[0].get("v")
        if price_str is None:
            return None

        price = float(price_str)
        logger.debug(f"[CryptoCom] index_price {instrument} = {price}")
        return price

    except (KeyError, ValueError, IndexError) as e:
        logger.warning(f"[CryptoCom] index_price 파싱 실패: {e}")
        return None


def get_btc_basis() -> dict:
    """
    T4-1 BTC Basis Spread 시그널 수집.

    공식: basis_spread = (mark - index) / index * 100

    Returns:
        dict:
          {
            "success": bool,
            "mark":        float | None,
            "index":       float | None,
            "basis_spread": float | None,   # %
            "state":       "Premium" | "Normal" | "Discount" | "Unknown",
            "score":       int,  # 1~3
            "error":       str | None,
          }
    """
    logger.info("[CryptoCom] T4-1 BTC Basis 수집 시작")

    mark = get_mark_price(DEFAULT_MARK_SYMBOL)
    index = get_index_price(DEFAULT_INDEX_SYMBOL)

    if mark is None or index is None:
        logger.warning(
            f"[CryptoCom] T4-1 실패: mark={mark}, index={index}"
        )
        return {
            "success": False,
            "mark": mark,
            "index": index,
            "basis_spread": None,
            "state": "Unknown",
            "score": 2,
            "error": "mark 또는 index 수집 실패",
        }

    if index == 0:
        logger.error("[CryptoCom] index 가격이 0 — 나눗셈 불가")
        return {
            "success": False,
            "mark": mark,
            "index": index,
            "basis_spread": None,
            "state": "Unknown",
            "score": 2,
            "error": "index price = 0",
        }

    basis_spread = round((mark - index) / index * 100, 4)

    # State 판정
    if basis_spread > 1.0:
        state, score = "Premium", 3
    elif basis_spread < -1.0:
        state, score = "Discount", 1
    else:
        state, score = "Normal", 2

    logger.info(
        f"[CryptoCom] T4-1 완료: basis={basis_spread:+.4f}% → {state} (score={score})"
    )

    return {
        "success": True,
        "mark": mark,
        "index": index,
        "basis_spread": basis_spread,
        "state": state,
        "score": score,
        "error": None,
    }
