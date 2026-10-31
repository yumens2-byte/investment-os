"""DN2026_0086 — 생성 cron 이 KST 토요일 아침이고, 그 시각에 월~금 미국장이 모두 끝나 있는지."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from gen.nuna_weekly import calendar_gate, collect, config

ROOT = Path(__file__).resolve().parents[3]
GEN_YML = ROOT / ".github" / "workflows" / "nuna_weekly_gen.yml"


def _cron() -> tuple[int, int, int]:
    m = re.search(r'^\s*-\s*cron:\s*"(\d+) (\d+) \* \* (\d)"', GEN_YML.read_text(encoding="utf-8"), re.M)
    assert m, "gen cron 없음"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def test_gen_cron_is_saturday_morning_kst():
    minute, hour_utc, dow_utc = _cron()
    # 2026-10-30 은 UTC 금요일(cron dow 5)
    base = datetime(2026, 10, 25 + dow_utc, hour_utc, minute, tzinfo=UTC)
    kst = base.astimezone(config.KST)
    assert kst.isoweekday() == calendar_gate.PUBLISH_WEEKDAY == 6
    assert (kst.hour, kst.minute) < (config.PUBLISH_HOUR_KST, config.PUBLISH_MINUTE_KST)


def test_cron_time_is_after_friday_close_in_both_dst_states():
    minute, hour_utc, _ = _cron()
    for saturday in (date(2026, 10, 31), date(2026, 11, 7)):   # 서머타임 / 표준시
        gate = calendar_gate.evaluate(saturday)
        friday = saturday - timedelta(days=1)
        run_at = datetime(friday.year, friday.month, friday.day, hour_utc, minute, tzinfo=UTC)
        collect.require_closed_sessions(gate, run_at.astimezone(config.KST))   # 예외 없음
        assert gate.publish_date == saturday and gate.us_days[-1] == friday
