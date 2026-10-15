"""테스트 격리 — preflight 이력(B안)·원장 동기화가 실데이터를 오염시키지 않게 한다.

원인(2026-10-15 실측): assert_token_ready 에 이력 적재를 넣은 뒤, 합성 계정을 쓰는
기존 테스트들이 gen/data/tiktok_token_history.json 에 가짜 만료시각을 기록했다
(2026-01-01T01:00:00.000Z 등). 테스트는 어떤 운영 파일도 건드리지 않는다.
"""
import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_hero_state(tmp_path_factory):
    root = tmp_path_factory.mktemp("hero_state")
    os.environ["HERO_TOKEN_HISTORY_FILE"] = str(root / "tiktok_token_history.json")
    os.environ["HERO_STATE_SYNC"] = "0"
    os.environ.setdefault("HERO_STATE_FILE", str(root / "publish_state.json"))
    yield str(root)


# --- B안 후속(2026-10-15): next_run 주입이 테스트 중 실 스케줄러 조회/실 캐시를 건드리지 않게 격리
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _isolate_next_run(tmp_path, monkeypatch):
    from gen.hero_shorts.tiktok import next_run as _nx
    monkeypatch.setenv(_nx.ENV_PIN, "1")
    monkeypatch.setenv(_nx.ENV_NEXT_RUN, "2026-10-20T01:30:00.000Z")
    monkeypatch.setenv(_nx.ENV_SOURCE, "pin")
    monkeypatch.setenv(_nx.ENV_CACHE, str(tmp_path / "next_run_cache.json"))
    monkeypatch.setenv(_nx.ENV_SCHEDULE_CMD, "/bin/false")
