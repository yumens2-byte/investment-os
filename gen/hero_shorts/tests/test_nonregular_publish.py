"""W11 — 비정기(시험) 발행 플래그 검증.
`--nonregular` 이 YoutubeTarget.regular_publish 로 전달되어 Y-G4 의
정기 public 강제만 해제되는지(visibility 유효집합 검사는 유지).
네트워크 0 · 원장 쓰기 0 · 시크릿 0.
"""
import argparse

from gen.hero_shorts.publish_targets import runner as R
from gen.hero_shorts.publish_targets.youtube import gates as ygates


def _ns(**kw):
    base = dict(visibility="private", first_comment=None, nonregular=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_flag_wiring_nonregular_true():
    targets = R._target_instances(["youtube"], _ns())
    assert targets["youtube"].regular_publish is False


def test_flag_wiring_default_is_regular():
    ns = argparse.Namespace(visibility="public", first_comment=None, nonregular=False)
    targets = R._target_instances(["youtube"], ns)
    assert targets["youtube"].regular_publish is True


def test_parser_default_and_flag():
    ap = R.build_parser()
    assert ap.parse_args(["publish", "--ep", "ep88"]).nonregular is False
    assert ap.parse_args(["--nonregular", "publish", "--ep", "ep88"]).nonregular is True


def test_yg4_nonregular_releases_public_only_for_valid_values():
    assert ygates.gate_yg4("private", regular_publish=False)["ok"] is True
    assert ygates.gate_yg4("unlisted", regular_publish=False)["ok"] is True
    assert ygates.gate_yg4("private", regular_publish=True)["ok"] is False
    assert ygates.gate_yg4("bogus", regular_publish=False)["ok"] is False
