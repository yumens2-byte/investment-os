"""발행 자격 선별기 — 원장 기반 게이트 A-G1/G3/G4 평가(오프라인, 전송 0회).

실행 주체: .github/workflows/zernio_publish_runner2.yml (2시간 cron + 수동)
입력: ENV_STATE(기본 state/publish_state.json), SELECT_EPS(쉼표 목록, 선택)
출력: selection.json — {"candidates": [{ep, platforms}], "skipped": [{ep, reason}]}

게이트:
  A-G1 승인    — approval.status == "APPROVED" 만 통과(HOLD·무응답·null 전부 스킵)
  A-G3 시도 예산 — 플랫폼별 attempts < 3
  A-G4 속도 제한 — run당 회차 1건, 플랫폼별 당일(UTC) 시도 4건 이하

A-G0(계정 헬스)·A-G2(발행 게이트)는 워크플로우 단계에서 평가한다 — 원장 밖 정보라서다.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_ATTEMPTS = 3
RUN_EP_CAP = 1
DAILY_ATTEMPT_CAP = 4
PLATFORMS = ("youtube", "threads")


def today_utc() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def attempts_today(entry: Optional[Dict[str, Any]]) -> int:
    day = today_utc()
    n = 0
    for a in (entry or {}).get("attempts") or []:
        at = str((a or {}).get("at") or "")
        if at.startswith(day):
            n += 1
    return n


def select(ledger: Dict[str, Any], *, eps: Optional[List[str]] = None,
           run_ep_cap: int = RUN_EP_CAP,
           daily_attempt_cap: int = DAILY_ATTEMPT_CAP,
           max_attempts: int = MAX_ATTEMPTS) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for ep in sorted(ledger.keys()):
        if ep.startswith("_"):
            continue                       # 메타 키(_schema 등) 제외
        if eps and ep not in eps:
            continue
        rec = ledger[ep]
        if not isinstance(rec, dict):
            continue

        approval = rec.get("approval") or {}
        if approval.get("status") != "APPROVED":
            skipped.append({"ep": ep, "gate": "A-G1",
                            "reason": f"승인 아님 status={approval.get('status')!r}"})
            continue

        targets = rec.get("targets") or {}
        plats: List[str] = []
        for plat in PLATFORMS:
            t = targets.get(plat) or {}
            if t.get("status") == "published":
                continue                   # 재발행 금지(G2)
            used = len(t.get("attempts") or [])
            if used >= max_attempts:
                skipped.append({"ep": ep, "gate": "A-G3",
                                "reason": f"{plat} 시도 상한 도달({used}>={max_attempts})"})
                continue
            if attempts_today(t) >= daily_attempt_cap:
                skipped.append({"ep": ep, "gate": "A-G4",
                                "reason": f"{plat} 당일 시도 한도 도달({daily_attempt_cap})"})
                continue
            plats.append(plat)

        if not plats:
            if not any(s["ep"] == ep for s in skipped):
                skipped.append({"ep": ep, "gate": "-",
                                "reason": "발행 대상 플랫폼 없음(모두 published)"})
            continue
        if len(candidates) >= run_ep_cap:
            skipped.append({"ep": ep, "gate": "A-G4",
                            "reason": f"run당 회차 상한({run_ep_cap}) — 다음 run에서 재평가"})
            continue
        candidates.append({"ep": ep, "platforms": plats})

    return {"candidates": candidates, "skipped": skipped}


def main() -> int:
    ledger_path = os.environ.get("HERO_PUBLISH_STATE", "state/publish_state.json")
    eps = [e for e in (os.environ.get("SELECT_EPS") or "").split(",") if e]
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    out = select(ledger, eps=eps or None)
    Path("selection.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
