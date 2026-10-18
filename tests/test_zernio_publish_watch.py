"""zernio_publish_watch.py 오프라인 회귀 테스트 — 네트워크 스텁으로 검증한다.

검증 항목:
  1. 상태 파일이 없어도 최초 run이 기준선 등록(알림 없음)으로 성공한다
  2. 변화 없는 재관측은 알림 0건 + 상태 파일 무수정(커밋 노이즈 억제)
  3. published 전이는 알림 1건(플랫폼 URL 포함) + 상태 갱신
"""

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "gen" / "hero_shorts" / "monitor" / "zernio_publish_watch.py"

POST_PENDING = {"post": {"_id": "AAA", "status": "publishing",
                         "platforms": [{"platform": "threads", "status": "pending"}]}}
POST_PUBLISHED = {"post": {"_id": "AAA", "status": "published",
                           "platforms": [{"platform": "threads", "status": "published",
                                          "platformPostUrl": "https://www.threads.net/@x/post/1"}]}}
HEALTH_OK = {"accounts": [{"platform": "threads", "status": "healthy",
                           "canPost": True, "tokenValid": True,
                           "needsReconnect": False, "issues": []}]}


def _load_module(monkeypatch, tmp_path, post_body):
    spec = importlib.util.spec_from_file_location("zpw_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "targets.json").write_text(
        json.dumps({"posts": [{"post_id": "AAA", "label": "ep88 1차"}]}), encoding="utf-8")
    mod.TARGETS_PATH = tmp_path / "targets.json"
    mod.STATE_PATH = tmp_path / "state.json"
    mod.ALERTS_PATH = tmp_path / "alerts.txt"

    def fake_get_json(path, api_key):
        if path.startswith("/posts/"):
            return post_body, 200
        return HEALTH_OK, 200

    monkeypatch.setattr(mod, "get_json", fake_get_json)
    monkeypatch.setenv("ZERNIO_API_KEY", "dummy")
    return mod


def test_first_run_baselines_silently(monkeypatch, tmp_path):
    mod = _load_module(monkeypatch, tmp_path, POST_PENDING)
    assert mod.main() == 0
    state = json.loads(mod.STATE_PATH.read_text(encoding="utf-8"))
    assert state["posts"]["AAA"]["entry_status"] == "pending"
    assert mod.ALERTS_PATH.read_text(encoding="utf-8") == ""


def test_unchanged_poll_does_not_rewrite_state(monkeypatch, tmp_path):
    mod = _load_module(monkeypatch, tmp_path, POST_PENDING)
    mod.main()
    before = mod.STATE_PATH.read_text(encoding="utf-8")
    assert mod.main() == 0
    assert mod.STATE_PATH.read_text(encoding="utf-8") == before, "무변화 run이 파일을 재기록했다"
    assert mod.ALERTS_PATH.read_text(encoding="utf-8") == ""


def test_published_transition_alerts_with_url(monkeypatch, tmp_path):
    mod = _load_module(monkeypatch, tmp_path, POST_PENDING)
    mod.main()

    def fake_published(path, api_key):
        if path.startswith("/posts/"):
            return POST_PUBLISHED, 200
        return HEALTH_OK, 200

    monkeypatch.setattr(mod, "get_json", fake_published)
    assert mod.main() == 0
    alerts = mod.ALERTS_PATH.read_text(encoding="utf-8")
    assert "발행 완료" in alerts
    assert "https://www.threads.net/@x/post/1" in alerts
    state = json.loads(mod.STATE_PATH.read_text(encoding="utf-8"))
    assert state["posts"]["AAA"]["entry_status"] == "published"


def test_disabled_target_is_never_polled(monkeypatch, tmp_path):
    mod = _load_module(monkeypatch, tmp_path, POST_PENDING)
    mod.TARGETS_PATH.write_text(json.dumps({"posts": [
        {"post_id": "AAA", "label": "활성"},
        {"post_id": "BBB", "label": "ep89 대기", "enabled": False},
        {"post_id": "", "label": "id 누락 슬롯"},
    ]}), encoding="utf-8")
    calls = []

    def spy(path, api_key):
        calls.append(path)
        if path.startswith("/posts/"):
            return POST_PENDING, 200
        return HEALTH_OK, 200

    monkeypatch.setattr(mod, "get_json", spy)
    assert mod.main() == 0
    polled = [c for c in calls if c.startswith("/posts/")]
    assert polled == ["/posts/AAA"], f"비활성 슬롯이 폴링됨: {polled}"
    state = json.loads(mod.STATE_PATH.read_text(encoding="utf-8"))
    assert set(state["posts"]) == {"AAA"}
