"""
gen/nuna_weekly/budget.py — 유료 호출 예산 게이트 (설계서 6장, DB 함수 nuna_reserve_cost)
=====================================================================================
사용 규칙: 모든 유료 호출은 아래 형태만 허용한다.

    with budget.charge(step="clips", provider="fal", model=EP, units=5, unit="sec",
                       est_usd=0.35) as ch:
        url, req_id = backend.run(...)
        ch.actual(usd=0.35, request_id=req_id)      # 생략 시 est_usd 로 확정

    - 예약 실패(상한 초과) → BudgetExceeded, 호출 자체가 일어나지 않는다.
    - 호출 예외 → 과금 여부 확인 불가 → 보수적으로 est_usd 로 charged (설계서 6장 2항).
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from . import config
from .db import BudgetExceeded  # noqa: F401  (호출부 재노출)

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.budget")


class _Charge:
    def __init__(self, est_usd: float) -> None:
        self.usd = est_usd
        self.units: float | None = None
        self.request_id: str | None = None
        self.actual_known = False

    def actual(self, usd: float | None = None, units: float | None = None,
               request_id: str | None = None) -> None:
        if usd is not None:
            self.usd = float(usd)
        self.units = units
        self.request_id = request_id
        self.actual_known = True


class Budget:
    def __init__(self, store, episode_id: str, fx_rate: float | None = None) -> None:
        self.store = store
        self.episode_id = episode_id
        self.fx = fx_rate or self._resolve_fx()
        log.info("[NunaWeekly:budget] v%s 시작 · 환율 %.2f · 상한 회당 ₩%d / 월 ₩%d",
                 VERSION, self.fx, config.budget_episode_krw(), config.budget_month_krw())

    def _resolve_fx(self) -> float:
        """최신 daily_snapshots.usdkrw — 없으면 보수적 상수(NUNA_FX_FALLBACK)."""
        from datetime import datetime

        try:
            today = datetime.now(config.KST).date().isoformat()
            row = self.store.latest_row("daily_snapshots", "snapshot_date", today)
            v = float(row["usdkrw"]) if row and row.get("usdkrw") else None
        except Exception as e:  # 조회 실패도 폴백 — 상한 쪽으로 보수적
            log.warning("환율 조회 실패 → 폴백 사용: %s", type(e).__name__)
            v = None
        return v if v and v > 0 else config.fx_fallback()

    @contextmanager
    def charge(self, step: str, provider: str, model: str, units: float, unit: str,
               est_usd: float) -> Iterator[_Charge]:
        cid = self.store.reserve_cost(
            self.episode_id, step, provider, model, units, unit, est_usd, self.fx,
            config.budget_episode_krw(), config.budget_month_krw())
        ch = _Charge(est_usd)
        try:
            yield ch
        except Exception:
            if ch.actual_known:
                self.store.settle_cost(cid, "charged", usd=ch.usd, units=ch.units, fx_rate=self.fx,
                                       request_id=ch.request_id)
                log.warning("응답 처리 실패 — 확인된 사용량 정산: %s/%s $%.4f", step, provider, ch.usd)
            else:
                self.store.settle_cost(cid, "charged", usd=est_usd, fx_rate=self.fx)
                log.warning("유료 호출 실패 — 과금 여부 불명, 예약액으로 확정: %s/%s $%.4f", step, provider, est_usd)
            raise
        self.store.settle_cost(cid, "charged", usd=ch.usd, units=ch.units, fx_rate=self.fx,
                               request_id=ch.request_id)
        log.info("비용 확정: %s/%s $%.4f (₩%d)", step, provider, ch.usd, round(ch.usd * self.fx))


def estimate_episode_usd(duration: int) -> float:
    """기본 1회 생성 추정액 (설계서 6장) — PoC 비교용."""
    tts = duration * config.TTS_TOKENS_PER_SEC * config.PRICE_TTS_OUT_PER_MTOK / 1e6
    script = 8000 * config.PRICE_CLAUDE_IN_PER_MTOK / 1e6 + 2500 * config.PRICE_CLAUDE_OUT_PER_MTOK / 1e6
    images = 6 * config.PRICE_IMAGE_USD
    clips = 2 * config.CLIP_SECONDS * config.PRICE_CLIP_USD_PER_SEC
    return round(tts + script + images + clips, 4)
