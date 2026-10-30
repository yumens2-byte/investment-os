"""
gen/nuna_weekly/fal_jobs.py — fal 유료 작업 실행·재개 (images · clips 공용)
==========================================================================
run_paid(fal, budget, jobs, key, ...) -> FalResult

    1) 원장(jobs) 또는 로컬 pending 에 같은 endpoint·payload 작업 기록이 있으면
       → 재제출·비용 예약 없이 재조회만 한다. 비용은 최초 제출 시 이미 원장에 확정됐다.
         (nuna_cost_ledger.request_id UNIQUE — 같은 request_id 로 두 번 정산할 수 없다)
    2) 없으면 → budget.charge 안에서 제출. 제출 직후 request_id 를 비용 원장에,
       작업 기록(request_id·status_url·response_url)을 jobs 에 남긴 뒤 폴링한다.
    2-1) 원장 기록이 있는데 endpoint·payload 가 다르면 → FAL_JOB_MISMATCH 로 차단(진행 중 작업 폐기·재과금 방지).
    3) 대기 상한 초과 → FalPendingTimeout(persisted=작업 기록 보존 여부). 원격 작업은 진행 중일 수 있다.

jobs 는 get(key)/save(key, job) 을 가진 객체(pipeline._FalJobs: assets_json.fal_pending). None 이면
로컬 pending 만 쓴다(단위 테스트·dummy). 작업 기록은 단계가 성공할 때 pipeline 이 지운다 —
일부 구간만 끝난 뒤 실패해도 새 러너에서 완료 작업을 재과금 없이 다시 받기 위해서다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from .backends import FalResult
from .backends.fal import FalError, FalPendingTimeout, is_resumable, load_pending, request_signature

VERSION = "1.0.0"
log = logging.getLogger("nuna_weekly.fal_jobs")


class FalJobs(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def save(self, key: str, job: dict[str, Any]) -> None: ...


def run_paid(fal: Any, budget: Any, jobs: FalJobs | None, key: str, *, step: str, endpoint: str,
             payload: dict[str, Any], out_path: Path, pending_path: Path, units: float, unit: str,
             est_usd: float, max_wait_sec: float) -> FalResult:
    sig = request_signature(payload)
    known = jobs.get(key) if jobs is not None else None
    if known is not None and not is_resumable(known, endpoint, sig):
        # 기록된 작업은 이미 과금 정산됐다. 프롬프트·캐논·기준 이미지 변경 후 재실행이 진행 중 작업을 버리고
        # 재과금하지 않도록 차단한다 — 대사 후 assets_json.fal_pending 에서 해당 키를 지워야 신규 제출.
        raise FalError(f"FAL_JOB_MISMATCH: {key} 원장 작업(request_id={known.get('request_id')})과 "
                       "endpoint·payload 불일치 — 재제출 차단, 대사 후 fal_pending 정리 필요")
    if known is None and is_resumable(load_pending(Path(pending_path)), endpoint, sig):
        known = load_pending(Path(pending_path))
    if known is not None:
        log.warning("%s 기존 fal 작업 재조회(재제출·재과금 없음): request_id=%s", key, known.get("request_id"))
        try:
            return fal.run(endpoint, payload, out_path, pending_path, max_wait_sec=max_wait_sec,
                           pending_job=known, submit_allowed=False)
        except FalPendingTimeout as e:
            e.persisted = jobs is not None and jobs.get(key) is not None
            raise

    with budget.charge(step=step, provider="fal", model=endpoint, units=units, unit=unit,
                       est_usd=est_usd) as ch:

        def submitted(job: dict[str, Any]) -> None:
            ch.submitted(job.get("request_id"))
            if jobs is not None:
                jobs.save(key, job)

        try:
            res = fal.run(endpoint, payload, out_path, pending_path, max_wait_sec=max_wait_sec,
                          on_submit=submitted)
        except FalPendingTimeout as e:
            e.persisted = jobs is not None and jobs.get(key) is not None
            raise
        ch.actual(request_id=res.request_id)
    return res
