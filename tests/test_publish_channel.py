"""FB-1 (2026-10-05): 발행 채널 선택(all | x | face) 격리 테스트.

검증 원칙: 채널별로 서로 영향이 없어야 한다.
  - all  : X·TG 동작은 v1.31.0 과 동일 + FB 는 FACE_ENABLED 일 때만, 실패해도 결과 불변
  - x    : X 만 (TG·FB·DLQ 미호출)
  - face : FB 만 (X·TG·DLQ·X 중복검사 미호출)
실제 외부 요청은 하지 않는다 (발행 함수 전부 mock).
"""
import ast
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_view
from config.settings import channel_allows

ROOT = Path(__file__).resolve().parents[1]

SAMPLE_DATA = {
    "market_snapshot": {"sp500": -0.5, "vix": 18.2, "us10y": 4.1, "oil": 70},
    "market_regime": {"market_regime": "Risk-Off", "market_risk_level": "MEDIUM"},
    "etf_allocation": {"allocation": {"SPY": 30}},
    "output_helpers": {"session_type": "morning"},
}


# ── T8 channel_allows ─────────────────────────────────────────
@pytest.mark.parametrize("channel,x,tg,face", [
    ("all", True, True, True),
    ("x", True, False, False),
    ("face", False, False, True),
    ("ALL", True, True, True),
    ("tg", False, False, False),
    ("", False, False, False),
    ("bogus", False, False, False),
])
def test_channel_allows_matrix(channel, x, tg, face):
    assert channel_allows("x", channel) is x
    assert channel_allows("tg", channel) is tg
    assert channel_allows("face", channel) is face


# ── run_view 하네스 ───────────────────────────────────────────
class Harness:
    """run_view.run 이 호출하는 외부 경계를 전부 mock 한다."""

    def __init__(self, tmp_path, *, face_enabled=False, duplicate=False,
                 fb_status="ok", x_success=True, fb_raises=False, data=None,
                 earnings_enabled=False):
        self.tmp_path = tmp_path
        self.face_enabled = face_enabled
        self.duplicate = duplicate
        self.fb_status = fb_status
        self.x_success = x_success
        self.fb_raises = fb_raises
        self.data = data if data is not None else dict(SAMPLE_DATA)
        self.earnings_enabled = earnings_enabled
        self.m = {}

    def __enter__(self):
        st = self._stack = ExitStack()
        m = self.m
        img = self.tmp_path / "dash.png"
        img.write_bytes(b"png")

        def P(target, **kw):
            m[target.rsplit(".", 1)[-1]] = st.enter_context(patch(target, **kw))

        P("core.dlq.get_queue_size", return_value=1)
        P("core.dlq.process_queue", return_value={"ok": 1})
        P("core.json_builder.load_core_data", return_value={"data": self.data})
        P("core.validator.validate_data", return_value={"status": "PASS"})
        P("core.validator.validate_output", return_value={"status": "PASS"})
        P("publishers.x_formatter.generate_ai_tweet", return_value="AI 트윗 본문")
        P("publishers.x_formatter.format_image_tweet", return_value="이미지 트윗")
        P("publishers.x_formatter.format_thread_auto", side_effect=lambda t, s, d: [t])
        P("core.duplicate_checker.is_duplicate", return_value=self.duplicate)
        P("core.duplicate_checker.record_published")
        P("publishers.image_generator.generate_image", return_value=str(img))
        P("publishers.x_publisher.publish_tweet",
          return_value={"success": self.x_success, "tweet_id": "T1" if self.x_success else None})
        P("publishers.x_publisher.publish_tweet_with_image",
          return_value={"success": self.x_success, "tweet_id": "T1" if self.x_success else None})
        P("publishers.x_publisher.publish_thread", return_value={"success": True, "tweet_ids": ["T1"]})
        P("publishers.telegram_publisher.send_message", return_value=[{"ok": True}])
        P("publishers.telegram_publisher.send_photo", return_value=[{"ok": True}])
        P("publishers.telegram_publisher.send_document", return_value=[{"ok": True}])
        P("publishers.telegram_publisher.format_free_signal",
          return_value="🌅 <b>Morning</b>\n💎 <i>풀버전 대시보드 → 유료 채널</i>\n#ETF")
        P("engines.earnings_checker.get_today_earnings", return_value={"success": False})
        P("core.streamer_dedupe.check_streamer_duplicate", return_value={"allow": True})
        P("core.streamer_dedupe.record_streamer_publish")
        P("publishers.translator.publish_multilingual", return_value={})
        P("core.fb_history.FB_HISTORY_FILE", new=self.tmp_path / "fb_history.json")
        if self.fb_raises:
            P("publishers.facebook_publisher.publish_page_post", side_effect=RuntimeError("boom"))
        else:
            P("publishers.facebook_publisher.publish_page_post",
              return_value={"success": self.fb_status == "ok", "status": self.fb_status,
                            "kind": "photo", "post_id": "FB1" if self.fb_status == "ok" else "",
                            "reason": ""})
        st.enter_context(patch.object(run_view, "FACE_ENABLED", self.face_enabled))
        st.enter_context(patch.object(run_view, "FACE_SESSIONS", ("morning", "narrative", "full")))
        st.enter_context(patch.object(run_view, "DRY_RUN", False))
        st.enter_context(patch.object(run_view, "MORNING_EARNINGS_ENABLED", self.earnings_enabled))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False

    def x_calls(self):
        return (self.m["publish_tweet"].call_count
                + self.m["publish_tweet_with_image"].call_count
                + self.m["publish_thread"].call_count)

    def tg_calls(self):
        return (self.m["send_message"].call_count
                + self.m["send_photo"].call_count
                + self.m["send_document"].call_count)


# ── T9 run_view 채널 게이트 ───────────────────────────────────
def test_all_with_face_disabled_matches_previous_behavior(tmp_path):
    with Harness(tmp_path, face_enabled=False) as h:
        res = run_view.run(mode="tweet", session="morning", channel="all")
    assert res["success"] is True and res["tweet_id"] == "T1"
    assert h.m["process_queue"].call_count == 1          # DLQ 처리
    assert h.m["is_duplicate"].call_count == 1
    assert h.x_calls() == 1 and h.tg_calls() >= 1
    assert h.m["record_published"].call_count == 1
    h.m["publish_page_post"].assert_not_called()
    assert res["fb_status"] == "skipped" and res["fb_reason"] == "face_disabled"


def test_all_with_face_enabled_publishes_three_channels(tmp_path):
    with Harness(tmp_path, face_enabled=True) as h:
        res = run_view.run(mode="tweet", session="morning", channel="all")
    assert h.x_calls() == 1 and h.tg_calls() >= 1
    h.m["publish_page_post"].assert_called_once()
    fb_text = h.m["publish_page_post"].call_args.args[0]
    assert "<b>" not in fb_text and "유료 채널" not in fb_text
    assert h.m["publish_page_post"].call_args.kwargs["image_path"].endswith("dash.png")
    assert res["success"] is True and res["fb_status"] == "ok" and res["fb_post_id"] == "FB1"


@pytest.mark.parametrize("fb_status,fb_raises", [("failed", False), ("unknown", False), ("ok", True)])
def test_all_fb_failure_does_not_change_x_tg_result(tmp_path, fb_status, fb_raises):
    with Harness(tmp_path, face_enabled=True, fb_status=fb_status, fb_raises=fb_raises) as h:
        res = run_view.run(mode="tweet", session="morning", channel="all")
    assert res["success"] is True and res["tweet_id"] == "T1"   # X 결과 기준 유지
    assert h.x_calls() == 1 and h.tg_calls() >= 1
    assert h.m["record_published"].call_count == 1
    assert res["fb_status"] in ("failed", "unknown")


def test_all_x_failure_does_not_block_tg_or_fb(tmp_path):
    with Harness(tmp_path, face_enabled=True, x_success=False) as h:
        res = run_view.run(mode="tweet", session="morning", channel="all")
    assert res["success"] is False                    # 기존과 동일: X 실패 → success False
    assert h.tg_calls() >= 1                          # TG 는 그대로 발행
    h.m["publish_page_post"].assert_called_once()     # FB 도 독립 수행
    assert h.m["record_published"].call_count == 0


def test_channel_x_only_x(tmp_path):
    with Harness(tmp_path, face_enabled=True) as h:
        res = run_view.run(mode="tweet", session="morning", channel="x")
    assert res["success"] is True
    assert h.x_calls() == 1 and h.tg_calls() == 0
    h.m["process_queue"].assert_not_called()
    h.m["publish_page_post"].assert_not_called()
    h.m["publish_multilingual"].assert_not_called()
    assert h.m["record_published"].call_count == 1


def test_channel_x_big_tech_tweets_only_x(tmp_path):
    # DN2026_0095: 스위치 활성 시 기존 동작(v1.32.0) 그대로
    with Harness(tmp_path, earnings_enabled=True) as h, \
         patch("engines.earnings_checker.get_today_earnings",
               return_value={"success": True, "big_tech": ["NVDA"]}), \
         patch("engines.stock_analyzer.analyze_big_tech_earnings",
               return_value=[{"success": True, "tweet": "NVDA 실적", "ticker": "NVDA"}]):
        run_view.run(mode="tweet", session="morning", channel="x")
    assert h.m["publish_tweet"].call_count == 1          # C-17 X 트윗
    assert h.tg_calls() == 0


# ── DN2026_0095 morning 실적 경로 스위치 ─────────────────────
_BIG_TECH = {"success": True, "big_tech": ["NVDA"], "tg_line": "📅 실적: NVDA(AMC)",
             "earnings": ["NVDA(AMC)"]}
_C17 = [{"success": True, "tweet": "NVDA 실적", "tg_text": "NVDA 실적 TG", "ticker": "NVDA"}]


@pytest.mark.parametrize("channel", ["all", "x"])
def test_morning_earnings_disabled_by_default(tmp_path, channel):
    """기본(비활성): 실적 목록 조회·C-17 생성·발행이 일어나지 않는다."""
    with Harness(tmp_path) as h, \
         patch("engines.earnings_checker.get_today_earnings", return_value=_BIG_TECH) as ge, \
         patch("engines.stock_analyzer.analyze_big_tech_earnings", return_value=_C17) as ab:
        res = run_view.run(mode="tweet", session="morning", channel=channel)
    assert res["success"] is True
    ge.assert_not_called()
    ab.assert_not_called()
    assert [c for c in h.m["publish_tweet"].call_args_list if c.args and c.args[0] == "NVDA 실적"] == []
    sent = [c.args[0] for c in h.m["send_message"].call_args_list if c.args]
    assert "📅 실적: NVDA(AMC)" not in sent and "NVDA 실적 TG" not in sent


def test_morning_disabled_keeps_main_outputs_identical(tmp_path):
    """레이아웃 불변(실적 없음 조건): 스위치 비활성/활성 두 경우 X·TG·다국어·FB 호출 인자가 동일.

    하네스 기본값은 get_today_earnings 실패 → 활성이어도 실적 출력이 없다.
    즉 이 테스트는 스위치 판별이 아니라 '스위치 도입이 기존 출력을 바꾸지 않음'을 검증한다.
    """
    calls = {}
    for enabled in (False, True):
        sub = tmp_path / str(enabled)
        sub.mkdir()
        with Harness(sub, face_enabled=True, earnings_enabled=enabled) as h:
            run_view.run(mode="tweet", session="morning", channel="all")
        calls[enabled] = (
            # 이미지 경로는 테스트 임시 폴더라 제외, 본문 텍스트만 비교
            [c.args[0] for c in h.m["publish_tweet_with_image"].call_args_list],
            [c.args for c in h.m["publish_thread"].call_args_list],
            [c.args for c in h.m["send_message"].call_args_list],
            h.m["send_photo"].call_count,
            h.m["send_document"].call_count,
            [c.args for c in h.m["publish_multilingual"].call_args_list],
            [c.args[0] for c in h.m["publish_page_post"].call_args_list],
        )
    assert calls[False] == calls[True]


@pytest.mark.parametrize("session", ["intraday", "close"])
@pytest.mark.parametrize("enabled", [False, True])
def test_non_morning_sessions_never_call_earnings(tmp_path, session, enabled):
    """morning 외 세션은 스위치와 무관하게 실적 경로를 타지 않는다(기존 동작 동일)."""
    with Harness(tmp_path, earnings_enabled=enabled), \
         patch("engines.earnings_checker.get_today_earnings", return_value=_BIG_TECH) as ge, \
         patch("engines.stock_analyzer.analyze_big_tech_earnings", return_value=_C17) as ab:
        run_view.run(mode="tweet", session=session, channel="all")
    ge.assert_not_called()
    ab.assert_not_called()


def test_morning_earnings_enabled_all_publishes_x_and_tg(tmp_path):
    with Harness(tmp_path, earnings_enabled=True) as h, \
         patch("engines.earnings_checker.get_today_earnings", return_value=_BIG_TECH), \
         patch("engines.stock_analyzer.analyze_big_tech_earnings", return_value=_C17):
        run_view.run(mode="tweet", session="morning", channel="all")
    assert len([c for c in h.m["publish_tweet"].call_args_list if c.args and c.args[0] == "NVDA 실적"]) == 1
    sent = [c.args[0] for c in h.m["send_message"].call_args_list if c.args]
    assert "📅 실적: NVDA(AMC)" in sent and "NVDA 실적 TG" in sent


@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("false", False), ("1", False), ("yes", False),
    ("true", True), ("TRUE", True), (" true ", True),
])
def test_morning_earnings_flag_parsing(raw, expected):
    import os
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "MORNING_EARNINGS_ENABLED"}
    if raw is not None:
        env["MORNING_EARNINGS_ENABLED"] = raw
    # 로컬 .env(load_dotenv) 영향 차단 — dotenv 를 무동작 스텁으로 대체해 env 값만으로 판정
    code = (
        "import sys, types; m = types.ModuleType('dotenv'); "
        "m.load_dotenv = lambda *a, **k: False; sys.modules['dotenv'] = m; "
        "from config.settings import MORNING_EARNINGS_ENABLED as v; print(v)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[-1]
    assert out == str(expected)


def test_channel_face_only_fb_even_when_disabled(tmp_path):
    with Harness(tmp_path, face_enabled=False) as h:
        res = run_view.run(mode="tweet", session="morning", channel="face")
    assert res["success"] is True and res["publish_channel"] == "face"
    assert h.x_calls() == 0 and h.tg_calls() == 0
    h.m["process_queue"].assert_not_called()
    h.m["is_duplicate"].assert_not_called()
    h.m["record_published"].assert_not_called()
    h.m["publish_page_post"].assert_called_once()


@pytest.mark.parametrize("channel,expected_yt", [("all", 1), ("x", 1), ("face", 0)])
def test_streamer_tweet_follows_x_gate(tmp_path, channel, expected_yt):
    data = {**SAMPLE_DATA, "streamer_consensus": {"tweet": "유튜버 요약", "direction": "UP"}}
    with Harness(tmp_path, data=data) as h:
        run_view.run(mode="tweet", session="morning", channel=channel)
    yt_calls = [c for c in h.m["publish_tweet"].call_args_list if c.args and c.args[0] == "유튜버 요약"]
    assert len(yt_calls) == expected_yt
    assert h.m["check_streamer_duplicate"].call_count == expected_yt


def test_streamer_tweet_blocked_by_fact_guard_is_not_published(tmp_path):
    """F3: 가드가 tweet 을 비우면 Step 6-YT 는 X 발행을 하지 않는다."""
    data = {**SAMPLE_DATA, "streamer_consensus": {"tweet": "", "direction": "UP",
                                                  "fact_guard": {"blocked": True}}}
    with Harness(tmp_path, data=data) as h:
        run_view.run(mode="tweet", session="morning", channel="all")
    assert h.m["check_streamer_duplicate"].call_count == 0
    assert h.m["publish_tweet"].call_count + h.m["publish_tweet_with_image"].call_count == 1  # 본 트윗만


def test_channel_face_blocked_second_time_same_day(tmp_path):
    with Harness(tmp_path) as h:
        first = run_view.run(mode="tweet", session="morning", channel="face")
        second = run_view.run(mode="tweet", session="morning", channel="face")
    assert first["success"] is True
    assert second["success"] is False and second["fb_status"] == "skipped"
    assert second["reason"].startswith("fb_skipped:fb_ok_exists")
    assert h.m["publish_page_post"].call_count == 1


def test_channel_face_failure_returns_false(tmp_path):
    with Harness(tmp_path, fb_status="failed") as h:
        res = run_view.run(mode="tweet", session="morning", channel="face")
    assert res["success"] is False and res["reason"].startswith("fb_failed")
    assert h.x_calls() == 0 and h.tg_calls() == 0


def test_face_session_not_in_whitelist(tmp_path):
    with Harness(tmp_path) as h:
        res = run_view.run(mode="tweet", session="intraday", channel="face")
    h.m["publish_page_post"].assert_not_called()
    assert res["fb_reason"] == "session_not_allowed:intraday" and res["success"] is False


def test_invalid_channel_blocks_everything(tmp_path):
    with Harness(tmp_path, face_enabled=True) as h:
        res = run_view.run(mode="tweet", session="morning", channel="tg")
    assert res == {"success": False, "reason": "invalid_channel", "channel": "tg"}
    assert h.x_calls() == 0 and h.tg_calls() == 0
    h.m["publish_page_post"].assert_not_called()
    h.m["process_queue"].assert_not_called()


def test_all_duplicate_without_fb_returns_exactly_as_before(tmp_path):
    with Harness(tmp_path, face_enabled=False, duplicate=True) as h:
        res = run_view.run(mode="tweet", session="morning", channel="all")
    assert res == {"success": False, "reason": "duplicate_detected", "text_preview": "AI 트윗 본문"}
    assert h.x_calls() == 0 and h.tg_calls() == 0
    h.m["generate_image"].assert_not_called()
    h.m["publish_page_post"].assert_not_called()


def test_all_duplicate_still_allows_fb_independently(tmp_path):
    with Harness(tmp_path, face_enabled=True, duplicate=True) as h:
        res = run_view.run(mode="tweet", session="morning", channel="all")
    assert res["success"] is False and res["reason"] == "duplicate_detected"
    assert h.x_calls() == 0 and h.tg_calls() == 0
    h.m["record_published"].assert_not_called()
    h.m["publish_page_post"].assert_called_once()
    assert res["fb_status"] == "ok"


def test_face_narrative_preserves_visual_history(tmp_path):
    hist = tmp_path / "narrative_visual_history.json"
    hist.write_text('["card"]', encoding="utf-8")

    def fake_select(data):
        hist.write_text('["card", "chart"]', encoding="utf-8")   # select_visual 의 이력 기록 모사
        return str(tmp_path / "dash.png"), "chart"

    with Harness(tmp_path) as h, \
         patch("engines.narrative_engine.generate_narrative",
               return_value={"narrative": "해설 본문", "source": "gemini"}), \
         patch("engines.narrative_engine.build_narrative_posts", return_value=["해설 트윗"]), \
         patch("engines.narrative_engine.format_narrative_telegram",
               return_value="📝 <b>시장 해설</b>\n해설 본문"), \
         patch("publishers.narrative_visual.select_visual", side_effect=fake_select), \
         patch("publishers.narrative_visual._history_path", return_value=hist):
        res = run_view.run(mode="tweet", session="narrative", channel="face")
    assert res["success"] is True
    assert hist.read_text(encoding="utf-8") == '["card"]'          # X 로테이션 이력 불변
    assert h.m["publish_page_post"].call_args.args[0] == "📝 시장 해설\n해설 본문"


# ── T13 run_market TG 게이트 (정적 검사) ──────────────────────
def _calls_guarded_by_channel(src: str, func_names: set) -> list:
    """func_names 호출 중 조상 If 의 조건에 channel_allows 가 없는 호출 목록."""
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    unguarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in func_names:
            cur, guarded = node, False
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, ast.If) and "channel_allows" in ast.unparse(cur.test):
                    guarded = True
                    break
            if not guarded:
                unguarded.append((node.func.id, node.lineno))
    return unguarded


def test_run_market_tg_and_rank_detection_are_channel_gated():
    src = (ROOT / "run_market.py").read_text(encoding="utf-8")
    gated = {"send_message", "detect_rank_change", "record_daily", "store_all_daily_data"}
    assert _calls_guarded_by_channel(src, gated) == []
    assert src.count("channel_allows(\"tg\")") == 5


# ── T11 main.yml 구조 ─────────────────────────────────────────
def _workflow():
    d = yaml.safe_load((ROOT / ".github/workflows/main.yml").read_text(encoding="utf-8"))
    return d, (d.get("on") or d.get(True))


def test_workflow_channel_input_and_env():
    d, on = _workflow()
    ch = on["workflow_dispatch"]["inputs"]["channel"]
    assert ch["type"] == "choice" and ch["default"] == "all"
    assert ch["options"] == ["all", "x", "face"]
    env = d["env"]
    assert env["PUBLISH_CHANNEL"] == "${{ github.event.inputs.channel || 'all' }}"
    assert env["FACE_PAGE_ID"] == "${{ secrets.FACE_PAGE_ID }}"
    assert env["FACE_PAGE_TOKEN"] == "${{ secrets.FACE_PAGE_TOKEN }}"
    assert env["FACE_ENABLED"] == "${{ vars.FACE_ENABLED || 'false' }}"
    assert env["FACE_SESSIONS"] == "${{ vars.FACE_SESSIONS || 'morning,narrative,full' }}"
    assert env["MORNING_EARNINGS_ENABLED"] == "${{ vars.MORNING_EARNINGS_ENABLED || 'false' }}"  # DN2026_0095


@pytest.mark.parametrize("job", ["morning", "full_dashboard", "narrative"])
def test_workflow_fb_history_cache_order(job):
    d, _ = _workflow()
    steps = d["jobs"][job]["steps"]
    names = [s.get("name") or s.get("run") or s.get("uses") for s in steps]
    run_idx = next(i for i, n in enumerate(names) if n and str(n).startswith("python main.py run all"))
    r = names.index("Restore fb history cache")
    s = names.index("Save fb history cache")
    assert r < run_idx < s
    assert steps[r]["uses"] == "actions/cache/restore@v4"
    assert steps[s]["uses"] == "actions/cache/save@v4" and steps[s]["if"] == "always()"
    assert steps[r]["with"]["path"] == steps[s]["with"]["path"] == "data/published/fb_history.json"
    assert steps[r]["with"]["key"] == steps[s]["with"]["key"] == \
        "fb-history-${{ github.ref }}-${{ github.run_id }}"


def test_workflow_cron_and_job_if_unchanged():
    d, on = _workflow()
    crons = [c["cron"] for c in on["schedule"]]
    assert crons == ["36 21 * * 0-4", "36 2 * * 1-5", "36 9 * * 1-5",
                     "*/10 13-14 * * 1-5", "*/10 15-21 * * 0-4"]
    assert "github.event.schedule == '36 21 * * 0-4'" in d["jobs"]["morning"]["if"]
    assert "github.event.schedule == '36 2 * * 1-5'" in d["jobs"]["narrative"]["if"]
    assert "github.event.schedule == '36 9 * * 1-5'" in d["jobs"]["full_dashboard"]["if"]


def test_workflow_pilot_runs_new_tests():
    d, _ = _workflow()
    runs = " ".join(str(s.get("run", "")) for s in d["jobs"]["pilot_test"]["steps"])
    assert "tests/test_facebook_publisher.py" in runs
    assert "tests/test_publish_channel.py" in runs
    assert "tests/test_fg_display.py" in runs
    assert "tests/test_streamer_fact_guard.py" in runs
