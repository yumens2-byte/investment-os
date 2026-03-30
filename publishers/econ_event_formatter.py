"""
publishers/econ_event_formatter.py
=====================================
경제지표 발표 즉시 알람 포맷

지원 지표: CPI, PCE, FOMC, JOBS, GDP, PPI
"""

INDICATOR_META = {
    "FEDFUNDS": {
        "name":  "기준금리",
        "emoji": "🏦",
        "unit":  "%",
        "tags":  "#기준금리 #FOMC #ETF #금리",
    },
    "T10YIE": {
        "name":  "10년 기대인플레이션",
        "emoji": "📊",
        "unit":  "%",
        "tags":  "#인플레이션 #ETF #미국증시",
    },
    "BAMLH0A0HYM2": {
        "name":  "HY 스프레드",
        "emoji": "⚡",
        "unit":  "%",
        "tags":  "#신용리스크 #ETF #채권",
    },
    "T10Y2Y": {
        "name":  "수익률 곡선(10Y-2Y)",
        "emoji": "📉",
        "unit":  "%",
        "tags":  "#수익률곡선 #ETF #경기침체",
    },
    "DCOILWTICO": {
        "name":  "WTI 유가",
        "emoji": "🛢️",
        "unit":  "$",
        "tags":  "#유가 #XLE #ETF",
    },
}

# 지표별 ETF 영향 매핑
INDICATOR_ETF_IMPACT = {
    "FEDFUNDS": {
        "up":   {"TLT": "하락 예상", "QQQM": "하락 압력"},
        "down": {"TLT": "상승 기대", "QQQM": "수혜 예상"},
    },
    "T10Y2Y": {
        "up":   {"TLT": "수혜"},
        "down": {"XLE": "주의", "ITA": "주의"},
    },
    "BAMLH0A0HYM2": {
        "up":   {"TLT": "안전자산 선호", "XLE": "위험"},
        "down": {"QQQM": "위험선호 회복"},
    },
    "DCOILWTICO": {
        "up":   {"XLE": "수혜"},
        "down": {"XLE": "주의"},
    },
}


def format_econ_event(
    indicator_id: str,
    prev_value: float,
    new_value: float,
    regime: str = "—",
    signal: str = "HOLD",
) -> str:
    """
    경제지표 변화 발표 포맷 (X 트윗용, 280자 이내)
    """
    meta = INDICATOR_META.get(indicator_id, {
        "name": indicator_id, "emoji": "📊", "unit": "", "tags": "#경제지표 #ETF"
    })

    name   = meta["name"]
    emoji  = meta["emoji"]
    unit   = meta["unit"]
    tags   = meta["tags"]

    change = new_value - prev_value
    sign   = "▲" if change > 0 else "▼"
    direction = "up" if change > 0 else "down"

    # ETF 영향
    impacts = INDICATOR_ETF_IMPACT.get(indicator_id, {}).get(direction, {})
    impact_lines = [f"→ {etf}: {desc}" for etf, desc in list(impacts.items())[:2]]

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴"}
    sig_e = SIGNAL_EMOJI.get(signal, "⚪")

    lines = [
        f"{emoji} {name} 변화 감지",
        "",
        f"이전: {prev_value:.2f}{unit}",
        f"현재: {new_value:.2f}{unit}  {sign}{abs(change):.2f}{unit}",
        "",
    ]
    if impact_lines:
        lines.append("💡 ETF 영향")
        lines.extend(impact_lines)
        lines.append("")
    lines.append(f"{sig_e} AI Signal: {signal}  |  Regime: {regime}")
    lines.append("")
    lines.append(tags)

    tweet = "\n".join(lines)
    return tweet[:280]


def format_econ_event_telegram(
    indicator_id: str,
    prev_value: float,
    new_value: float,
    regime: str = "—",
    signal: str = "HOLD",
) -> str:
    """텔레그램 HTML 포맷"""
    meta = INDICATOR_META.get(indicator_id, {
        "name": indicator_id, "emoji": "📊", "unit": "", "tags": "#경제지표 #ETF"
    })

    name   = meta["name"]
    emoji  = meta["emoji"]
    unit   = meta["unit"]
    tags   = meta["tags"]

    change = new_value - prev_value
    sign   = "▲" if change > 0 else "▼"
    direction = "up" if change > 0 else "down"
    impacts = INDICATOR_ETF_IMPACT.get(indicator_id, {}).get(direction, {})

    SIGNAL_EMOJI = {"BUY": "🟢", "HOLD": "🟡", "REDUCE": "🔴"}
    sig_e = SIGNAL_EMOJI.get(signal, "⚪")

    lines = [
        f"{emoji} <b>{name} 변화 감지</b>",
        "",
        f"이전: <b>{prev_value:.2f}{unit}</b>",
        f"현재: <b>{new_value:.2f}{unit}</b>  {sign}{abs(change):.2f}{unit}",
        "",
    ]
    if impacts:
        lines.append("💡 <b>ETF 영향</b>")
        for etf, desc in list(impacts.items())[:3]:
            lines.append(f"→ <b>{etf}</b>: {desc}")
        lines.append("")
    lines.append(f"{sig_e} Signal: <b>{signal}</b>  |  {regime}")
    lines.append("")
    lines.append(tags)

    return "\n".join(lines)
