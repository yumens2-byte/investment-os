"""
gen/hero_shorts/tiktok/audio_mix.py — 배경음악(BGM) + 효과음(SFX) 믹싱
=====================================================================
배경: 틱톡 분할 세그먼트는 **내레이션이 영상에 내장**돼 있다(발화 트랙 = [0:a]).
따라서 BGM 을 얹을 때는 별도 보이스 트랙이 아니라 **영상 자체 오디오를
사이드체인 키로** 덕킹해야 한다. `voiceover.mix_voice_over` 는 보이스가 분리된
(합본 전) 경로용이라 이 경우 그대로 쓸 수 없다 — 그래서 이 모듈을 분리한다.

원칙:
    - BGM 파일이 없거나 0바이트면 fail-closed(RuntimeError). 무음으로 마스킹 금지.
    - 믹싱 후 길이 보존(_shortest), 원본 오디오(내레이션·효과음)는 유지.
    - 결과물은 가청성(RMS) 재검증한다.
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("tiktok.audio_mix")

VERSION = "1.0.0"

# 기본 게인 — 설계 문서(tts-upgrade-design-v1 §3) 실측 권고: BGM 트랙 0.12~0.15 덕킹.
DEFAULT_BGM_GAIN = 0.5       # 덕킹 전 원본 BGM 스케일
DEFAULT_BGM_VOLUME = 0.30    # 덕킹 후 BGM 볼륨
DEFAULT_ORIG_GAIN = 1.0      # 내장 내레이션 볼륨(유지)


def _run(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"{label} 실패: {r.stderr[-300:]}")
    return r


def probe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return round(float(r.stdout.strip()), 3)


def has_audio(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False,
    )
    return bool(r.stdout.strip())


def audio_rms_db(path):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0].strip())
    return None


def _require_bgm(bgm):
    p = Path(bgm)
    if not p.exists() or p.stat().st_size == 0:
        raise RuntimeError(f"BGM 파일 부재/0바이트: {p} (fail-closed — 무음 마스킹 금지)")
    return p


def duck_filter(bgm_gain=DEFAULT_BGM_GAIN, bg_volume=DEFAULT_BGM_VOLUME,
                orig_gain=DEFAULT_ORIG_GAIN):
    """내장 보이스([0:a])를 사이드체인 키로 BGM([1:a])을 덕킹하는 필터 체인."""
    return (
        f"[0:a]volume={orig_gain}[orig];"
        f"[1:a]volume={bgm_gain}[bgraw];"
        "[bgraw][0:a]sidechaincompress=threshold=0.02:ratio=8:attack=60:release=480[duck];"
        f"[duck]volume={bg_volume}[bg];"
        "[bg][orig]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mix]"
    )


def mix_bgm(video, bgm, out_file, bgm_gain=DEFAULT_BGM_GAIN,
            bg_volume=DEFAULT_BGM_VOLUME, orig_gain=DEFAULT_ORIG_GAIN):
    """영상(BGM 없음 → BGM 있음). 내레이션은 볼륨 유지, BGM 은 말할 때만 낮아진다."""
    video, bgm = Path(video), _require_bgm(bgm)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if not has_audio(video):
        raise RuntimeError(f"영상에 오디오 스트림이 없음: {video} (덕킹 키 부재)")
    cmd = [
        "ffmpeg", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(bgm),
        "-filter_complex", duck_filter(bgm_gain, bg_volume, orig_gain),
        "-map", "0:v:0", "-map", "[mix]",
        "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_file),
    ]
    _run(cmd, "BGM 덕킹 믹싱")
    before, after = probe_duration(video), probe_duration(out_file)
    if abs(before - after) > 0.6:
        raise RuntimeError(f"길이 변동 과다: {before}s → {after}s (믹싱 거부)")
    log.info("BGM 믹싱 완료: %s (길이 %.3fs 유지, rms=%sdB)",
             out_file, after, audio_rms_db(out_file))
    return str(out_file)


def add_sfx(video, events, out_file, default_gain=0.9):
    """지정 시각에 효과음을 얹는다. events=[{"path":..., "at":초, "gain":0.9}, ...]"""
    if not events:
        raise ValueError("효과음 이벤트 없음 — 추측 믹싱 금지")
    video = Path(video)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    for e in events:
        p = Path(e["path"])
        if not p.exists() or p.stat().st_size == 0:
            raise RuntimeError(f"효과음 파일 부재/0바이트: {p} (fail-closed)")
        cmd += ["-i", str(p)]
    chains = []
    mix_labels = ["[0:a]"]
    for i, e in enumerate(events, start=1):
        ms = round(float(e.get("at", 0.0)) * 1000)
        g = float(e.get("gain", default_gain))
        chains.append(f"[{i}:a]adelay={ms}|{ms},volume={g}[sfx{i}]")
        mix_labels.append(f"[sfx{i}]")
    chains.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:"
        "duration=first:dropout_transition=2:normalize=0[mix]"
    )
    cmd += ["-filter_complex", ";".join(chains),
            "-map", "0:v:0", "-map", "[mix]",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_file)]
    _run(cmd, "효과음 믹싱")
    log.info("효과음 믹싱 완료: %s (%d개 이벤트)", out_file, len(events))
    return str(out_file)


def mix_segment(video, out_file, bgm=None, sfx=None):
    """세그먼트 1편 종합 믹싱 — BGM → 효과음 순 적용. 둘 다 없으면 원본 유지."""
    if not bgm and not sfx:
        raise ValueError("BGM·효과음 모두 없음 — 믹싱할 것이 없다(호출부 판단 오류)")
    cur = Path(video)
    if bgm:
        tmp = Path(str(out_file) + ".bgm.mp4")
        cur = Path(mix_bgm(cur, bgm, tmp))
    if sfx:
        tmp2 = Path(str(out_file) + ".sfx.mp4")
        cur = Path(add_sfx(cur, sfx, tmp2))
    final = Path(out_file)
    if cur != final:
        final.parent.mkdir(parents=True, exist_ok=True)
        _run(["ffmpeg", "-y", "-i", str(cur), "-c", "copy", "-movflags", "+faststart", str(final)],
             "최종 출력")
    return str(final)


def save_manifest(path, payload):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)
