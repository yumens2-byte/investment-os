"""
collectors/crypto_com_client.py (v1.2.0)
=========================================
Phase 1A — Crypto.com REST API 클라이언트

변경이력:
  v1.2.0 (2026-04-07) get-valuations 엔드포인트로 마이그레이션
                      이전: /get-mark-price, /get-index-price (404 — REST에 없음)
                      수정: /get-valuations + valuation_type 파라미터
                      mark/index price는 WebSocket subscription 채널일 뿐
                      REST 공식 엔드포인트는 public/get-valuations
                      참조: https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html
                            #public-get-valuations
  v1.1.0 (2026-04-07) Base URL 중복 버그 수정 (실제로는 부분 수정에 그침)
  v1.0.0 초기 버전

용도:
  - T4-1 Crypto Basis Spread 시그널 수집
  - BTC Mark Price vs Index Price 차이 계산

API:
  - Base URL: https://api.crypto.com/exchange/v1
  - 인증: 불필요 (public endpoints만 사용)
  - Rate limit: public/get-valuations 100 req/sec/IP

엔드포인트:
  GET /public/get-valuations?instrument_name=BTCUSD-PERP&valuation_type=mark_price&count=1
  GET /public/get-valuations?instrument_name=BTCUSD-INDEX&valuation_type=index_price&count=1

응답 구조:
  {
    "id": -1,
    "method": "public/get-valuations",
    "code": 0,
    "result": {
      "instrument_name": "BTCUSD-INDEX",
      "data": [{"v": "68717.83000", "t": 1613547318000}]
    }
  }

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

VERSION = "1.2.0"

# Crypto.com Exchange API v1
# 공식 Production endpoint: https://api.crypto.com/exchange/v1/{method}
_BASE_URL = "https://api.crypto.com/exchange/v1"
_TIMEOUT_SEC = 5
_RETRY_COUNT = 3
_RETRY_BACKOFF = [1, 2, 4]

DEFAULT_PERP_INSTRUMENT = "BTCUSD-PERP"
DEFAULT_INDEX_INSTRUMENT = "BTCUSD-INDEX"

VALUATION_MARK = "mark_price"
VALUATION_INDEX = "index_price"


def _http_get_valuations(
    instrument_name: str,
    valuation_type: str,
    count: int = 1,
) -> Optional[dict]:
    """
    public/get-valuations 호출 (재시도 포함).

    Args:
        instrument_name: 상품명 (BTCUSD-PERP | BTCUSD-INDEX)
        valuation_type:  index_price | mark_price | funding_hist | funding_rate | estimated_funding_rate
        count: 반환 데이터 개수 (기본 1 = 최신 1건만)

    Returns:
        dict: {"code": 0, "result": {...}} 또는 None
    """
    import requests

    url = f"{_BASE_URL}/public/get-valuations"
    params = {
        "instrument_name": instrument_name,
        "valuation_type": valuation_type,
        "count": count,
    }
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
                    f"[CryptoCom] get-valuations({instrument_name}, {valuation_type}) "
                    f"HTTP {resp.status_code} (attempt {attempt + 1}/{_RETRY_COUNT})"
                )
                if attempt < _RETRY_COUNT - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
                    continue
                return None

            data = resp.json()
            code = data.get("code")
            if code != 0:
                last_error = f"code={code}"
                logger.warning(
                    f"[CryptoCom] get-valuations({instrument_name}, {valuation_type}) "
                    f"오류 코드: {code}"
                )
                return None

            return data

        except requests.Timeout:
            last_error = "timeout"
            logger.warning(
                f"[CryptoCom] get-valuations({instrument_name}, {valuation_type}) "
                f"타임아웃 (attempt {attempt + 1}/{_RETRY_COUNT})"
            )
            if attempt < _RETRY_COUNT - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
                continue
            return None

        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"[CryptoCom] get-valuations({instrument_name}, {valuation_type}) 예외: {e}"
            )
            return None

    logger.error(
        f"[CryptoCom] get-valuations({instrument_name}, {valuation_type}) "
        f"최종 실패: {last_error}"
    )
    return None


def _parse_valuation_value(raw: dict) -> Optional[float]:
    """
    get-valuations 응답에서 가격 값(v) 파싱.

    응답 구조:
      {
        "code": 0,
        "result": {
          "instrument_name": "BTCUSD-INDEX",
          "data": [{"v": "68717.83000", "t": 1613547318000}]
        }
      }
    """
    try:
        result = raw.get("result", {})
        items = result.get("data", [])
        if not items:
            logger.warning("[CryptoCom] get-valuations 응답에 data가 없음")
            return None

        first = items[0]
        if not isinstance(first, dict):
            return None

        value_str = first.get("v")
        if value_str is None:
            return None

        return float(value_str)

    except (KeyError, ValueError, IndexError, TypeError) as e:
        logger.warning(f"[CryptoCom] valuation 값 파싱 실패: {e}")
        return None


def get_mark_price(instrument: str = DEFAULT_PERP_INSTRUMENT) -> Optional[float]:
    """
    선물 mark 가격 조회 (public/get-valuations valuation_type=mark_price).

    Args:
        instrument: 상품명 (예: BTCUSD-PERP)

    Returns:
        float: mark 가격
        None: 실패
    """
    raw = _http_get_valuations(
        instrument_name=instrument,
        valuation_type=VALUATION_MARK,
        count=1,
    )
    if not raw:
        return None

    price = _parse_valuation_value(raw)
    if price is not None:
        logger.debug(f"[CryptoCom] mark_price {instrument} = {price}")
    return price


def get_index_price(instrument: str = DEFAULT_INDEX_INSTRUMENT) -> Optional[float]:
    """
    Underlying index 가격 조회 (public/get-valuations valuation_type=index_price).

    Args:
        instrument: 상품명 (예: BTCUSD-INDEX)

    Returns:
        float: index 가격
        None: 실패
    """
    raw = _http_get_valuations(
        instrument_name=instrument,
        valuation_type=VALUATION_INDEX,
        count=1,
    )
    if not raw:
        return None

    price = _parse_valuation_value(raw)
    if price is not None:
        logger.debug(f"[CryptoCom] index_price {instrument} = {price}")
    return price


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
            "basis_spread": float | None,
            "state":       "Premium" | "Normal" | "Discount" | "Unknown",
            "score":       int,
            "error":       str | None,
          }
    """
    logger.info(f"[CryptoCom v{VERSION}] T4-1 BTC Basis 수집 시작")

    mark = get_mark_price(DEFAULT_PERP_INSTRUMENT)
    index = get_index_price(DEFAULT_INDEX_INSTRUMENT)

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

    if basis_spread > 1.0:
        state, score = "Premium", 3
    elif basis_spread < -1.0:
        state, score = "Discount", 1
    else:
        state, score = "Normal", 2

    logger.info(
        f"[CryptoCom] T4-1 완료: mark={mark} index={index} "
        f"basis={basis_spread:+.4f}% → {state} (score={score})"
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
