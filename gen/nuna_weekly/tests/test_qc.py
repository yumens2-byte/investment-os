"""qc.py 테스트 — 실제 ffmpeg 로 소형 영상 생성 후 Q1~Q8 판정."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from gen.nuna_weekly import qc
from gen.nuna_weekly.config import DISCLAIMER
from gen.nuna_weekly.models import Facts, Script, ScriptSegment

PUB = date(2026, 10, 24)


def _ff(*args: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


def _video(p: Path, sec: float = 4.0, size: str = "1080x1920", audio: str = "sine",
           vcodec: str = "libx264") -> Path:
    src_a = (f"sine=frequency=440:duration={sec}:sample_rate=48000" if audio == "sine"
             else f"anullsrc=r=48000:cl=stereo:d={sec}")
    _ff("-f", "lavfi", "-i", f"color=c=navy:size={size}:rate=30:duration={sec}",
        "-f", "lavfi", "-i", src_a, "-c:v", vcodec, "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-ar", "48000", "-shortest", str(p))
    return p


def _script(**kw) -> Script:
    caps = {"instagram": f"이번 주 시장 정리 {DISCLAIMER}", "youtube": f"요약 {DISCLAIMER}"}
    base = dict(episode_id="t", title="미국증시 주간 브리핑",
                segments=[ScriptSegment("S2", 8, "S&P500 1.2% 상승", card_title="이번 주",
                                        card_lines=["S&P500 +1.2%"]),
                          ScriptSegment("S8", 8, f"{DISCLAIMER}.")],
                captions=caps)
    base.update(kw)
    return Script(**base)


FACTS = Facts(episode_id="t", source="direct", fetched_at="2026-10-24T08:00:00+09:00", days=[])


@pytest.fixture(scope="module")
def good_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("v")
    return _video(d / "main.mp4")


def test_all_pass_and_json(good_video):
    rep = qc.run(good_video, "main", 4.0, _script(), FACTS, PUB)
    assert rep.passed, rep.gates
    assert set(rep.gates) == {f"Q{i}" for i in range(1, 9)}
    assert rep.gates["Q4"]["detail"] == "검증기 미주입"
    assert rep.measured["telegram_ok"] is True
    j = good_video.parent / "qc" / "qc_main.json"
    data = json.loads(j.read_text(encoding="utf-8"))
    assert data["passed"] is True and set(data) == {"variant", "passed", "gates", "measured", "qc_version"}
    assert "http" not in j.read_text(encoding="utf-8")


def test_q2_duration(good_video):
    assert not qc.run(good_video, "main", 6.0, _script(), FACTS, PUB).gates["Q2"]["ok"]
    assert qc.run(good_video, "fb_cut", 70.0, _script(), FACTS, PUB).gates["Q2"]["ok"]


def test_q1_spec_and_q3_silent(tmp_path):
    p = _video(tmp_path / "bad.mp4", sec=3.5, size="720x1280", audio="silent")
    rep = qc.run(p, "main", 3.5, _script(), FACTS, PUB)
    assert not rep.gates["Q1"]["ok"] and "해상도" in rep.gates["Q1"]["detail"]
    assert not rep.gates["Q3"]["ok"]
    assert not rep.passed


def test_q4_verifier_variants(good_video):
    calls = []

    def ok_list(script, facts, pub):
        calls.append(pub)
        return []
    assert qc.run(good_video, "main", 4.0, _script(), FACTS, PUB, ok_list).gates["Q4"]["ok"]
    assert calls == [PUB]
    assert not qc.run(good_video, "main", 4.0, _script(), FACTS, PUB, lambda *a: ["9.9"]).gates["Q4"]["ok"]
    assert not qc.run(good_video, "main", 4.0, _script(), FACTS, PUB, lambda *a: (False, "x")).gates["Q4"]["ok"]

    def boom(*a):
        raise ValueError("bad")
    assert not qc.run(good_video, "main", 4.0, _script(), FACTS, PUB, boom).gates["Q4"]["ok"]


def test_q5_forbidden_and_links_not_leaked(good_video):
    s = _script(title="지금 매수하세요 https://evil.example.com/x?token=abc")
    rep = qc.run(good_video, "main", 4.0, s, FACTS, PUB)
    assert not rep.gates["Q5"]["ok"]
    assert "title" in rep.gates["Q5"]["detail"]["link_locations"]
    text = (good_video.parent / "qc" / "qc_main.json").read_text(encoding="utf-8")
    assert "evil.example.com" not in text and "token=abc" not in text


def test_q6_disclaimer(good_video):
    s = _script(captions={"instagram": "면책 없음", "youtube": f"요약 {DISCLAIMER}"})
    rep = qc.run(good_video, "main", 4.0, s, FACTS, PUB)
    assert rep.gates["Q6"]["detail"]["missing"] == ["caption.instagram"]
    s2 = _script(segments=[ScriptSegment("S8", 8, "끝")])
    assert "S8.narration" in qc.run(good_video, "main", 4.0, s2, FACTS, PUB).gates["Q6"]["detail"]["missing"]


def test_q7_limits(good_video):
    s = _script(title="가" * 101, captions={"threads": DISCLAIMER + "나" * 500})
    rep = qc.run(good_video, "main", 4.0, s, FACTS, PUB)
    over = rep.gates["Q7"]["detail"]["over"]
    assert any(o.startswith("title") for o in over) and any(o.startswith("caption.threads") for o in over)


def test_q8_size(good_video, monkeypatch):
    monkeypatch.setattr(qc, "IG_MAX_BYTES", 10)
    rep = qc.run(good_video, "main", 4.0, _script(), FACTS, PUB)
    assert not rep.gates["Q8"]["ok"] and not rep.passed


def test_missing_file(tmp_path):
    with pytest.raises(qc.QCError):
        qc.run(tmp_path / "nope.mp4", "main", 4.0, _script(), FACTS, PUB)
