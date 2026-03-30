"""
collectors/fear_greed.py
==========================
Fear & Greed Index 수집

소스 1 (primary):   alternative.me API (무료 공식)
소스 2 (fallback):  CNN API (비공식, 차단 가능)

반환값:
{
  "value": 18,
  "label": "Extreme Fear",   # Extreme Fear / Fear / Neutral / Greed / Extreme Greed
  "prev_value": 22,
  "prev_label": "Fear",
  "change": -4,
  "source": "alternative.me"
}
"""
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

TIMEOUT = 8


def _label(value: int) -> str:
    if value <= 24:
        return "Extreme Fear"
    elif value <= 44:
        return "Fear"
    elif value <= 55:
        return "Neutral"
    elif value <= 74:
        return "Greed"
    else:
        return "Extreme Greed"


def _label_emoji(label: str) -> str:
    return {
        "Extreme Fear": "😱",
        "Fear":         "😨",
        "Neutral":      "😐",
        "Greed":        "😊",
        "Extreme Greed":"🤑",
    }.get(label, "😐")


def _from_alternative_me() -> Optional[dict]:
    """alternative.me 공식 무료 API"""
    try:
        res = requests.get(
            "https://api.alternative.me/fng/?limit=2&format=json",
            timeout=TIMEOUT
        )
        data = res.json().get("data", [])
        if len(data) >= 2:
            cur  = data[0]
            prev = data[1]
            v    = int(cur["value"])
            pv   = int(prev["value"])
            return {
                "value":      v,
                "label":      cur.get("value_classification", _label(v)),
                "prev_value": pv,
                "prev_label": prev.get("value_classification", _label(pv)),
                "change":     v - pv,
                "source":     "alternative.me",
            }
    except Exception as e:
        logger.warning(f"[FearGreed] alternative.me 실패: {e}")
    return None


def _from_cnn() -> Optional[dict]:
    """CNN Fear & Greed API (비공식 fallback)"""
    try:
        res = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=TIMEOUT
        )
        js   = res.json()
        cur  = js["fear_and_greed"]
        hist = js.get("fear_and_greed_historical", {}).get("data", [])
        v    = round(float(cur["score"]))
        pv   = round(float(hist[-2]["y"])) if len(hist) >= 2 else v
        return {
            "value":      v,
            "label":      cur.get("rating", _label(v)),
            "prev_value": pv,
            "prev_label": _label(pv),
            "change":     v - pv,
            "source":     "cnn",
        }
    except Exception as e:
        logger.warning(f"[FearGreed] CNN fallback 실패: {e}")
    return None


def collect_fear_greed() -> Optional[dict]:
    """Fear & Greed Index 수집 (primary → fallback)"""
    result = _from_alternative_me() or _from_cnn()
    if result:
        emoji = _label_emoji(result["label"])
        result["emoji"] = emoji
        logger.info(
            f"[FearGreed] {result['value']}/100 {result['label']} "
            f"(전일대비 {result['change']:+d}) [{result['source']}]"
        )
    else:
        logger.warning("[FearGreed] 모든 소스 실패 — None 반환")
    return result
