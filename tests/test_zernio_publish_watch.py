"""zernio_publish_watch.py 오프라인 회귀 테스트 — 네트워크 스텁으로 검증한다.

검증 항목:
  1. 첫 관측은 기준선 등록만(알림 없음) + 상태 파일 생성
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
    return mod, mod.STATE_PATH, mod.ALERTS_PATH


def test_first_run_baselines_silently(monkeypatch, tmp_path):
    mod, state_path, alerts_path = _load_module(monkeypatch, tmp_path, POST_PENDING)
    assert mod.main() == 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["posts"]["AAA"]["entry_status"] == "pending"
    assert alerts_path.read_text(encoding="utf-8") == ""


def test_unchanged_poll_does_not_rewrite_state(monkeypatch, tmp_path):
    mod, state_path, alerts_path = _load_module(monkeypatch, tmp_path, POST_PENDING)
    mod.main()
    before = state_path.read_text(encoding="utf-8")
    assert mod.main() == 0
    assert state_path.read_text(encoding="utf-8") == before, "무변화 run이 파일을 재기록했다"
    assert alerts_path.read_text(encoding="utf-8") == ""


def test_published_transition_alerts_with_url(monkeypatch, tmp_path):
    mod, state_path, alerts_path = _load_module(monkeypatch, tmp_path, POST_PENDING)
    mod.main()
    mod2, _, _ = _load_module(monkeypatch, tmp_path, POST_PUBLISHED)
    # 동일 tmp_path 재사용 — 모듈 상수가 tmp_path 를 다시 가리키도록 이미 설정됨
    assert mod.main() == 0  # mod2 가 아닌 mod: STATE_PATH 는 동일 tmp_path
    alerts = alerts_path.read_text(encoding="utf-8")
    assert "발행 완료" in alerts
    assert "https://www.threads.net/@x/post/1" in alerts
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["posts"]["AAA"]["entry_status"] == "published"
