"""images.py · clips.py 단위 테스트 — DummyFal·MemoryStore, 네트워크 없음."""
from __future__ import annotations

import pytest

from gen.nuna_weekly import config, images
from gen.nuna_weekly.backends import FalResult
from gen.nuna_weekly.backends.fal import DummyFal
from gen.nuna_weekly.budget import Budget
from gen.nuna_weekly.clips import ClipError, generate_clips
from gen.nuna_weekly.db import MemoryStore
from gen.nuna_weekly.images import ImageError, generate_images
from gen.nuna_weekly.models import Script, ScriptSegment

EP = "2026-W43"


@pytest.fixture(autouse=True)
def _no_refs(monkeypatch, tmp_path):
    monkeypatch.delenv(images.REF_ENV, raising=False)
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "noassets")


def make_script(total: int) -> Script:
    return Script(episode_id=EP, title="t", captions={}, segments=[
        ScriptSegment(s.seg_id, s.seconds, "내레이션", image_prompt=f"scene {s.seg_id}",
                      clip_prompt=f"motion {s.seg_id}") for s in config.segments(total)])


def budget() -> Budget:
    return Budget(MemoryStore(), EP, fx_rate=1500.0)


@pytest.mark.parametrize("total,expected", [(60, ["S2", "S3", "S4", "S5", "S7"]),
                                            (70, ["S2", "S3", "S4", "S5", "S6", "S7"])])
def test_images_dummy(tmp_path, total, expected):
    fal, b = DummyFal(), budget()
    out = generate_images(EP, make_script(total), config.segments(total), fal, b, tmp_path)
    assert list(out) == expected
    ep, payload = fal.calls[0]
    assert ep == config.FAL_IMAGE_ENDPOINT
    assert payload["aspect_ratio"] == "9:16" and payload["output_format"] == "png" and payload["num_images"] == 1
    assert payload["prompt"].startswith("Iron Nuna") and payload["prompt"].endswith(images.PROMPT_SUFFIX)
    assert "scene S2" in payload["prompt"]
    assert [c["step"] for c in b.store.costs] == ["images"] * len(expected)
    assert b.store.costs[0]["unit"] == "image" and b.store.costs[0]["usd"] == config.PRICE_IMAGE_USD
    # 재실행 — 기존 파일 재사용, 추가 호출·과금 없음
    out2 = generate_images(EP, make_script(total), config.segments(total), fal, b, tmp_path)
    assert len(fal.calls) == len(expected) and len(b.store.costs) == len(expected)
    assert out2["S2"].remote_url == out["S2"].remote_url


class NamedFal(DummyFal):
    name = "fal"


def test_no_reference_real_backend(tmp_path):
    with pytest.raises(ImageError, match="기준 이미지 없음"):
        generate_images(EP, make_script(60), config.segments(60), NamedFal(), budget(), tmp_path)


def test_reference_env_and_files(tmp_path, monkeypatch):
    monkeypatch.setenv(images.REF_ENV, "https://a.test/1.png, https://a.test/2.png")
    assert images.reference_urls() == ["https://a.test/1.png", "https://a.test/2.png"]
    monkeypatch.setenv(images.REF_ENV, "http://a.test/1.png")
    with pytest.raises(ImageError):
        images.reference_urls()
    monkeypatch.delenv(images.REF_ENV)
    d = tmp_path / "assets"
    d.mkdir()
    (d / "nuna_ref_front.png").write_bytes(b"\x89PNG....")
    refs = images.reference_urls(d)
    assert len(refs) == 1 and refs[0].startswith("data:image/png;base64,")


def test_canon_file():
    c = images.load_canon()
    assert c["name"] == "Iron Nuna"
    assert {"red-and-gold armor", "helmet", "watermark"} <= set(c["forbidden"])
    assert c["negative_prompt"]


def test_clips_dummy(tmp_path):
    fal, b = DummyFal(), budget()
    sc, segs = make_script(60), config.segments(60)
    imgs = generate_images(EP, sc, segs, fal, b, tmp_path)
    n_img = len(fal.calls)
    out = generate_clips(EP, sc, segs, imgs, fal, b, tmp_path)
    assert list(out) == ["S1", "S8"]
    clip_calls = fal.calls[n_img:]
    assert clip_calls[0][1]["start_image_url"] == imgs["S2"].remote_url
    assert clip_calls[1][1]["start_image_url"] == imgs["S7"].remote_url
    p = clip_calls[0][1]
    assert p["generate_audio"] is False and p["duration"] == "5" and p["negative_prompt"]
    assert all(abs(a.seconds - 5) <= 0.5 for a in out.values())
    clip_costs = [c for c in b.store.costs if c["step"] == "clips"]
    assert len(clip_costs) == 2 and clip_costs[0]["units"] == 5 and clip_costs[0]["unit"] == "sec"
    assert clip_costs[0]["usd"] == pytest.approx(5 * config.PRICE_CLIP_USD_PER_SEC)
    generate_clips(EP, sc, segs, imgs, fal, b, tmp_path)   # 재사용
    assert len(fal.calls) == n_img + 2


class ShortFal(DummyFal):
    def run(self, endpoint, payload, out_path, pending_path):
        out_path.write_bytes(b"x" * 100)
        return FalResult(path=out_path, remote_url="https://dummy.invalid/x", request_id="dummy-x")


def test_clip_verify_fails(tmp_path):
    sc, segs = make_script(60), config.segments(60)
    imgs = generate_images(EP, sc, segs, DummyFal(), budget(), tmp_path)
    with pytest.raises(ClipError, match="10KB"):
        generate_clips(EP, sc, segs, imgs, ShortFal(), budget(), tmp_path / "c")


def test_clip_missing_start_image(tmp_path):
    with pytest.raises(ClipError, match="remote_url"):
        generate_clips(EP, make_script(60), config.segments(60), {}, DummyFal(), budget(), tmp_path)


def test_clip_audio_guard(tmp_path, monkeypatch):
    from gen.nuna_weekly import clips

    sc, segs = make_script(60), config.segments(60)
    imgs = generate_images(EP, sc, segs, DummyFal(), budget(), tmp_path)
    orig = clips.build_payload
    monkeypatch.setattr(clips, "build_payload", lambda p, u: {**orig(p, u), "generate_audio": True})
    fal = DummyFal()
    with pytest.raises(ClipError, match="generate_audio"):
        generate_clips(EP, sc, segs, imgs, fal, budget(), tmp_path / "c")
    assert fal.calls == []
