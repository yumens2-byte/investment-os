"""Convert Investment OS core data into a deterministic blog package."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from blog.domain import BlogPostPackage, SourceRef


KST = ZoneInfo("Asia/Seoul")
DISCLAIMER = (
    "이 글은 정보 제공을 목적으로 작성되었으며 특정 금융상품의 매수·매도를 "
    "권유하지 않습니다. 투자 판단과 결과에 대한 책임은 투자자 본인에게 있습니다."
)


def build_blog_post(
    core_envelope: dict[str, Any],
    session: str,
    *,
    created_at: datetime | None = None,
    content_version: int = 1,
) -> BlogPostPackage:
    """Build a PREPARE_ONLY package without calling an external publisher."""
    data = core_envelope.get("data", core_envelope)
    if not isinstance(data, dict):
        raise ValueError("core_data.data must be an object")

    created = _as_aware(created_at or datetime.now(timezone.utc))
    market_as_of = _parse_timestamp(
        core_envelope.get("timestamp")
        or data.get("timestamp")
        or created.isoformat()
    )
    publication_date = created.astimezone(KST).date().isoformat()

    snapshot = data.get("market_snapshot", {})
    regime_data = data.get("market_regime", {})
    signal_data = data.get("trading_signal", {})
    allocation = data.get("etf_allocation", {}).get("allocation", {})
    helpers = data.get("output_helpers", {})

    regime = str(regime_data.get("market_regime") or "시장 레짐 확인 필요")
    risk = str(regime_data.get("market_risk_level") or "UNKNOWN")
    signal = str(signal_data.get("trading_signal") or "HOLD")
    reason = str(signal_data.get("signal_reason") or "").strip()
    summary = str(
        helpers.get("one_line_summary")
        or reason
        or f"{regime} 구간에서 {signal} 전략을 점검합니다."
    ).strip()

    title = _normalize_space(
        f"{publication_date} 미국증시 점검: {regime} · {signal} 대응"
    )
    sources = _extract_sources(data)
    tags = _build_tags(regime, risk, signal, session)
    body = _build_body(
        publication_date=publication_date,
        market_as_of=market_as_of,
        snapshot=snapshot,
        regime=regime,
        risk=risk,
        signal=signal,
        reason=reason,
        summary=summary,
        allocation=allocation,
        headlines=helpers.get("top_headlines", []),
        sources=sources,
    )
    body_plain = _markdown_to_plain(body)

    source_data_hash = _sha256_json(core_envelope)
    content_hash = _sha256_json({
        "title": title,
        "body": body,
        "tags": tags,
        "sources": [source.url for source in sources],
        "content_version": content_version,
    })
    idempotency_key = _sha256_text(
        "|".join([
            "naver_blog",
            publication_date,
            session,
            content_hash,
        ])
    )
    job_id = f"naver-{publication_date}-{session}-{idempotency_key[:12]}"

    return BlogPostPackage(
        job_id=job_id,
        idempotency_key=idempotency_key,
        content_hash=content_hash,
        content_version=content_version,
        channel="naver_blog",
        session=session,
        publication_date_kst=publication_date,
        market_as_of=market_as_of,
        created_at=created,
        title=title,
        summary=summary,
        body_markdown=body,
        body_plain=body_plain,
        tags=tags,
        sources=sources,
        source_data_hash=source_data_hash,
        metadata={
            "publish_mode": "PREPARE_ONLY",
            "template_version": "naver-blog-v1",
        },
    )


def _build_body(
    *,
    publication_date: str,
    market_as_of: datetime,
    snapshot: dict[str, Any],
    regime: str,
    risk: str,
    signal: str,
    reason: str,
    summary: str,
    allocation: dict[str, Any],
    headlines: Any,
    sources: tuple[SourceRef, ...],
) -> str:
    lines = [
        f"# {publication_date} 미국시장 브리핑",
        "",
        f"> 데이터 기준: {market_as_of.astimezone(KST).strftime('%Y-%m-%d %H:%M KST')}",
        "",
        "## 한눈에 보기",
        "",
        summary,
        "",
        "## 시장 스냅샷",
        "",
        f"- S&P 500: {_format_value(snapshot.get('sp500'), suffix='%')}",
        f"- Nasdaq: {_format_value(snapshot.get('nasdaq'), suffix='%')}",
        f"- VIX: {_format_value(snapshot.get('vix'))}",
        f"- 미국 10년물 금리: {_format_value(snapshot.get('us10y'), suffix='%')}",
        f"- WTI: {_format_value(snapshot.get('oil'), prefix='$')}",
        f"- 달러 인덱스: {_format_value(snapshot.get('dollar_index'))}",
        "",
        "## 레짐과 대응",
        "",
        f"- 시장 레짐: **{regime}**",
        f"- 위험 수준: **{risk}**",
        f"- 시스템 시그널: **{signal}**",
    ]
    if reason:
        lines.extend(["", f"판단 근거: {reason}"])

    lines.extend(["", "## ETF 배분 관점", ""])
    if isinstance(allocation, dict) and allocation:
        for ticker, weight in sorted(
            allocation.items(),
            key=lambda item: _numeric_sort_key(item[1]),
            reverse=True,
        ):
            lines.append(f"- {ticker}: {_format_value(weight, suffix='%')}")
    else:
        lines.append("- 배분 데이터 확인 필요")

    cleaned_headlines = [
        _normalize_space(str(item))
        for item in headlines
        if _normalize_space(str(item))
    ] if isinstance(headlines, list) else []
    if cleaned_headlines:
        lines.extend(["", "## 주요 이슈", ""])
        lines.extend(
            f"{index}. {headline}"
            for index, headline in enumerate(cleaned_headlines[:5], start=1)
        )

    lines.extend(["", "## 확인할 리스크", ""])
    lines.append(
        "시장 데이터는 발표·거래 시점에 따라 변할 수 있으므로 게시 직전 "
        "기준시각과 주요 수치를 다시 확인해야 합니다."
    )

    if sources:
        lines.extend(["", "## 출처", ""])
        for source in sources:
            if source.url:
                lines.append(f"- [{source.name}]({source.url})")
            else:
                lines.append(f"- {source.name}")

    lines.extend(["", "---", "", DISCLAIMER])
    return "\n".join(lines).strip() + "\n"


def _extract_sources(data: dict[str, Any]) -> tuple[SourceRef, ...]:
    candidates: list[SourceRef] = []
    raw_sources = data.get("sources", [])
    if isinstance(raw_sources, dict):
        raw_sources = [
            {"name": name, "url": url}
            for name, url in raw_sources.items()
        ]
    if isinstance(raw_sources, list):
        for item in raw_sources:
            if isinstance(item, str):
                candidates.append(SourceRef(name=item))
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("source") or "").strip()
                url = str(item.get("url") or "").strip()
                if name or url:
                    candidates.append(SourceRef(name=name or url, url=url))

    source_detail = data.get("news_sentiment", {}).get("source_detail", [])
    if not source_detail:
        source_detail = data.get("source_detail", [])
    if isinstance(source_detail, list):
        for item in source_detail:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("source") or "").strip()
            url = str(item.get("url") or "").strip()
            if name or url:
                candidates.append(SourceRef(name=name or url, url=url))

    deduped: list[SourceRef] = []
    seen: set[tuple[str, str]] = set()
    for source in candidates:
        key = (source.name.casefold(), source.url)
        if key not in seen:
            seen.add(key)
            deduped.append(source)
    return tuple(deduped)


def _build_tags(regime: str, risk: str, signal: str, session: str) -> tuple[str, ...]:
    raw = [
        "미국증시",
        "미국주식",
        "ETF",
        _tag_token(regime),
        _tag_token(risk),
        _tag_token(signal),
        _tag_token(session),
    ]
    result: list[str] = []
    for item in raw:
        if item and item not in result:
            result.append(item)
    return tuple(result[:30])


def _tag_token(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value)


def _format_value(value: Any, *, prefix: str = "", suffix: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "확인 필요"
    if isinstance(value, int):
        rendered = f"{value:,}"
    else:
        rendered = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{prefix}{rendered}{suffix}"


def _numeric_sort_key(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float("-inf")
    return float(value)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _as_aware(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("market timestamp is required")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return _as_aware(datetime.fromisoformat(normalized))
    except ValueError as exc:
        raise ValueError(f"invalid market timestamp: {value}") from exc


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256_text(encoded)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _markdown_to_plain(value: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    text = re.sub(r"[*_>#-]", "", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip() + "\n"
