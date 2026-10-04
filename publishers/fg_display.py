"""
publishers/fg_display.py
========================
F1 (2026-10-05): Fear & Greed 표기 정정 — 주식(CNN) / 코인(alternative.me) 구분.

배경
  - data["fear_greed"] 는 collectors/fear_greed.collect_fear_greed() 결과로,
    1순위 alternative.me (코인 F&G), 실패 시 CNN 폴백이다. source 필드로 구분된다.
  - data["cnn_fg"] 는 collectors/cnn_fear_greed.collect_cnn_fg() 결과 (주식 F&G).
  - 기존 morning 발행물(X·TG·대시보드)은 data["fear_greed"] 를 출처 표기 없이
    「F&G」「시장심리」로 표시해, 주식 브리핑에 코인 지수가 주식 심리처럼 노출됐다.
    (2026-10-05 실측: 코인 65 Greed / 주식 CNN 31.2 Fear — 반대 구간)

규칙
  - 주식 F&G : cnn_fg(success & value) 우선. 없으면 fear_greed.source == "cnn" 인 경우만.
  - 코인 F&G : fear_greed.source == "alternative.me" 인 경우만.
  - source 가 없는 레거시 데이터는 출처 미상으로 보고 기존 표기("F&G")를 유지한다.
"""
from __future__ import annotations

VERSION = "1.0.0"

CRYPTO_SOURCE = "alternative.me"
STOCK_SOURCE = "cnn"

_EMOJI = {
    "Extreme Fear":  "😱",
    "Fear":          "😨",
    "Neutral":       "😐",
    "Greed":         "😊",
    "Extreme Greed": "🤑",
}


def _title(label: str) -> str:
    return " ".join(w.capitalize() for w in str(label or "").split())


def _num(value):
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def stock_fg(data: dict) -> dict | None:
    """주식 F&G {value:int, label:str, emoji:str} 또는 None."""
    cnn = (data or {}).get("cnn_fg") or {}
    if cnn.get("success") and _num(cnn.get("value")) is not None:
        label = _title(cnn.get("rating", ""))
        return {"value": _num(cnn.get("value")), "label": label, "emoji": _EMOJI.get(label, "😐")}

    fg = (data or {}).get("fear_greed") or {}
    if fg.get("source") == STOCK_SOURCE and _num(fg.get("value")) is not None:
        label = _title(fg.get("label", ""))
        return {"value": _num(fg.get("value")), "label": label, "emoji": _EMOJI.get(label, "😐")}
    return None


def crypto_fg(data: dict) -> dict | None:
    """코인 F&G {value:int, label:str, emoji:str, change:int} 또는 None."""
    fg = (data or {}).get("fear_greed") or {}
    if fg.get("source") != CRYPTO_SOURCE or _num(fg.get("value")) is None:
        return None
    label = fg.get("label", "")
    return {
        "value":  _num(fg.get("value")),
        "label":  label,
        "emoji":  fg.get("emoji") or _EMOJI.get(label, "😐"),
        "change": _num(fg.get("change")) or 0,
    }


def legacy_fg(data: dict) -> dict | None:
    """source 없는 레거시 fear_greed — 출처 미상이므로 기존 표기 유지용."""
    fg = (data or {}).get("fear_greed") or {}
    if fg.get("source") or _num(fg.get("value")) is None:
        return None
    label = fg.get("label", "")
    return {
        "value":  _num(fg.get("value")),
        "label":  label,
        "emoji":  fg.get("emoji") or _EMOJI.get(label, "😐"),
        "change": _num(fg.get("change")) or 0,
    }


def prompt_lines(data: dict) -> list[str]:
    """AI 프롬프트 데이터 라인 (출처 명시)."""
    lines = []
    s = stock_fg(data)
    c = crypto_fg(data)
    lg = legacy_fg(data)
    if s:
        lines.append(f"- 주식 F&G(CNN): {s['value']} ({s['label']})")
    if c:
        lines.append(f"- 코인 F&G(alternative.me, 주식 지표 아님): {c['value']} ({c['label']})")
    if lg and not s and not c:
        lines.append(f"- F&G: {lg['value']} ({lg['label']})")
    return lines


def compact_line(data: dict) -> str:
    """X 이미지 트윗용 한 줄. 예: '😨 주식 F&G 31 Fear · 코인 F&G 65 Greed (-2)'. 없으면 ''."""
    s = stock_fg(data)
    c = crypto_fg(data)
    parts = []
    lead = ""
    if s:
        lead = s["emoji"]
        parts.append(f"주식 F&G {s['value']} {s['label']}")
    if c:
        lead = lead or c["emoji"]
        chg = f" ({c['change']:+d})" if c["change"] else ""
        parts.append(f"코인 F&G {c['value']} {c['label']}{chg}")
    if not parts:
        lg = legacy_fg(data)
        if not lg:
            return ""
        chg = f" ({lg['change']:+d})" if lg["change"] else ""
        return f"{lg['emoji']} F&G: {lg['value']}/100 {lg['label']}{chg}"
    return f"{lead} " + " · ".join(parts)


def panel_title(data: dict) -> str:
    """대시보드 F&G 패널 제목 — data["fear_greed"] 의 출처를 그대로 표기."""
    source = ((data or {}).get("fear_greed") or {}).get("source")
    if source == CRYPTO_SOURCE:
        return "Crypto Fear &amp; Greed"
    if source == STOCK_SOURCE:
        return "Stock Fear &amp; Greed (CNN)"
    return "Fear &amp; Greed"
