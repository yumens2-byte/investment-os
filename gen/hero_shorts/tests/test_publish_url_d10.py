"""D-10 회귀 — 발행·원장 기록용 media_url 은 토큰 없는 래퍼 URL 이어야 한다.

2026-10-15 실측:
  러너1이 `upload_media()["public_url"]`(토큰 URL) 을 원장에 기록 →
  토큰 URL 은 ~2h 만에 403 (설계 가정 24h 와 불일치),
  Zernio 는 애초에 토큰 URL 을 "no file extension and no stored content type" 으로 거부.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from gen.hero_shorts import mediashare


def test_upload_media_exposes_wrapper_as_publish_url(monkeypatch, tmp_path):
    f = tmp_path / "ep99_final_voiced.mp4"
    f.write_bytes(b"\x00" * 16)

    def fake_gsk_call(args):
        if args[0] == "upload":
            return {"url": "https://www.genspark.ai/api/files/s/WRAPPER"}
        if args[0] == "download":
            return {"url": "https://www.genspark.ai/api/files/s/WRAPPER?token=TOKEN123"}
        return {}

    monkeypatch.setattr(mediashare, "_gsk_call", fake_gsk_call)
    r = mediashare.upload_media(f)

    assert r["publish_url"] == r["wrapper_url"] == "https://www.genspark.ai/api/files/s/WRAPPER"
    assert "token=" not in r["publish_url"]
    assert "token=" in r["public_url"]


def test_pipeline_records_publish_url_not_token(tmp_path):
    """파이프라인 소스에 기록용 토큰 URL 사용이 남아 있지 않은지 정적 검증."""
    src = (Path(__file__).resolve().parents[1] / "pipeline.py").read_text(encoding="utf-8")
    assert 'media["public_url"]' not in src, "원장/승인 경로에 토큰 URL 이 남아 있다"
    assert '["publish_url"]' in src
