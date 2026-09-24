"""
gen/hero_shorts/voiceover.py — 대사 계획·TTS·오디오 믹싱
===================================================
비용 정책:
    - backend='dummy' 는 0원 테스트 경로
    - backend='gsk'   는 실제 TTS 경로 (명시 호출 시만)

역할:
    1) 컷계획 JSON → 컷별 대사 계획 JSON
    2) 대사 계획 → 컷별 음성 WAV + 병합 WAV
    3) 최종 영상 + 병합 WAV → 발행용 혼합 MP4
"""
import json
import logging
import math
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("hero_shorts.voiceover")

ROLE_LINES = {
    "ESTABLISH_THREAT": "{title}. 시장 충격이 도시를 덮친다.",
    "ESCALATE": "불안이 번지고 흐름이 흔들린다. 지금은 버티기보다 재정렬이 필요하다.",
    "ENGAGE": "수호자는 정면 승부보다 질서를 되찾을 빈틈을 찾는다.",
    "CRISIS": "충격은 더 커지고, 잘못 묶인 자금은 연쇄 흔들림을 만든다.",
    "TURN": "핵심은 배분 전환이다. 필요한 곳으로 힘을 다시 보낸다.",
    "RESOLVE": "흐름은 서서히 안정된다. 위기는 끝이 아니라 다음 판단의 시작이다.",
    "AFTERMATH_CALM": "충격이 지나간 자리에서 피해와 균열이 드러난다.",
    "REGROUP": "남은 자원과 우선순위를 다시 정리해야 한다.",
    "NEXT_OMEN": "정리가 끝난 자리에도 다음 압박의 그림자는 남아 있다.",
    "FB_OPEN": "이 장면은 현재 판단을 만든 과거의 균열에서 시작된다.",
    "FB_BODY": "당시의 충격은 숫자보다 사람의 선택을 더 크게 흔들었다.",
    "FB_PEAK": "한 번의 붕괴는 오래 남는 기준을 만든다.",
    "FB_RETURN": "그래서 지금의 대응은 감정이 아니라 축적된 기준 위에서 나온다.",
}


def _safe_title(title: str) -> str:
    return str(title or "이 회차")


def line_for_role(role: str, title: str, episode_type: str, outcome: str) -> str:
    template = ROLE_LINES.get(role, "상황은 계속 변하고, 판단 기준은 더 정교해져야 한다.")
    text = template.format(title=_safe_title(title), type=episode_type or "UNKNOWN", outcome=outcome or "UNKNOWN")
    return " ".join(text.split())


def estimate_tts_duration(text: str, max_duration_sec: float, chars_per_sec: float = 6.5) -> float:
    base = max(2.0, len(text.strip()) / chars_per_sec + 0.8)
    return round(min(max_duration_sec, base), 2)


def build_dialogue_plan(plan: Dict) -> Dict:
    cuts = []
    title = plan.get("title", "")
    episode_type = plan.get("type", "")
    outcome = plan.get("outcome", "")
    for cut in plan.get("cuts", []):
        request = cut.get("request", {})
        max_duration = float(request.get("duration", 10))
        text = line_for_role(cut.get("role", ""), title, episode_type, outcome)
        cuts.append({
            "cut_no": cut.get("cut_no"),
            "role": cut.get("role"),
            "text": text,
            "target_duration_sec": estimate_tts_duration(text, max_duration),
            "max_duration_sec": max_duration,
        })
    payload = {
        "episode": plan.get("episode"),
        "title": title,
        "type": episode_type,
        "outcome": outcome,
        "cuts": cuts,
    }
    log.info("대사 계획 생성: ep=%s cuts=%d", payload.get("episode"), len(cuts))
    return payload


def save_dialogue_plan(plan_file, out_file) -> str:
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    payload = build_dialogue_plan(plan)
    out_path = Path(out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("대사 계획 저장: %s", out_path)
    return str(out_path)


def probe_duration(path) -> float:
    r = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe duration 실패: {r.stderr[:200]}")
    return float((r.stdout or "0").strip() or "0")


def has_audio_stream(path) -> bool:
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe audio stream 실패: {r.stderr[:200]}")
    return bool(r.stdout.strip())


def _dummy_tts(text: str, out_path, duration_sec: float):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    freq = 520 + (sum(ord(ch) for ch in text) % 180)
    duration = max(1.5, float(duration_sec))
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=24000:duration={duration}",
        "-c:a", "pcm_s16le", "-ac", "1",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"더미 TTS 생성 실패: {r.stderr[:400]}")
    return str(out_path)


def _gsk_tts(text: str, out_path, model: Optional[str] = None, speaker: Optional[str] = None):
    chosen_model = model or "elevenlabs/v4-tts"
    cmd = ["gsk", "audio_generation", text, "-m", chosen_model, "-o", str(out_path)]
    if speaker:
        cmd.extend(["--speaker", speaker])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gsk TTS 실패: {r.stderr[:300] or r.stdout[:300]}")
    if not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
        raise RuntimeError("gsk TTS 출력 파일 없음")
    return str(out_path)


def fit_audio_duration(in_path, out_path, target_duration_sec: float) -> str:
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, float(target_duration_sec))
    cmd = [
        "ffmpeg", "-y", "-i", str(in_path),
        "-af", "apad",
        "-t", f"{duration:.2f}",
        "-c:a", "pcm_s16le", "-ac", "1",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"오디오 길이 보정 실패: {r.stderr[:300]}")
    return str(out_path)


def synthesize_tts_clips(dialogue_plan_file, out_dir, backend="dummy", model=None, speaker=None) -> Dict:
    payload = json.loads(Path(dialogue_plan_file).read_text(encoding="utf-8"))
    raw_episode = str(payload.get("episode", "episode")).strip().lower().replace(" ", "")
    episode = raw_episode if raw_episode.startswith("ep") else f"ep{raw_episode}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for cut in payload.get("cuts", []):
        raw_clip_path = out_dir / f"{episode}_voice_cut{cut['cut_no']}_raw.wav"
        clip_path = out_dir / f"{episode}_voice_cut{cut['cut_no']}.wav"
        text = cut["text"]
        target_duration = float(cut.get("target_duration_sec", 3.0))
        segment_duration = float(cut.get("max_duration_sec", target_duration))
        if backend == "dummy":
            result = _dummy_tts(text, raw_clip_path, target_duration)
        elif backend == "gsk":
            result = _gsk_tts(text, raw_clip_path, model=model, speaker=speaker)
        else:
            raise ValueError(f"알 수 없는 TTS 백엔드: {backend}")
        fitted = fit_audio_duration(result, clip_path, segment_duration)
        clips.append({
            "cut_no": cut["cut_no"],
            "role": cut.get("role"),
            "text": text,
            "audio_file": fitted,
            "raw_audio_file": result,
            "duration_sec": round(probe_duration(fitted), 2),
            "target_duration_sec": target_duration,
            "segment_duration_sec": segment_duration,
        })
    merged = out_dir / f"{episode}_voice_merged.wav"
    concat_audio([c["audio_file"] for c in clips], merged)
    result = {
        "episode": payload.get("episode"),
        "backend": backend,
        "model": model,
        "speaker": speaker,
        "clips": clips,
        "merged_audio": str(merged),
        "merged_duration_sec": round(probe_duration(merged), 2),
    }
    manifest = out_dir / f"{episode}_voice_manifest.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("TTS 완료: ep=%s clips=%d merged=%s", payload.get("episode"), len(clips), merged)
    return result


def concat_audio(audio_paths: List[str], out_path) -> str:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{str(Path(p).resolve())}'" for p in audio_paths), encoding="utf-8")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(out_path)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"오디오 병합 실패: {r.stderr[:200]}")
    return str(out_path)


def mix_voice_over(final_video, voice_audio, out_file, original_audio_gain=0.22, voice_gain=1.5) -> str:
    final_video = Path(final_video)
    voice_audio = Path(voice_audio)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if has_audio_stream(final_video):
        filter_complex = (
            f"[0:a]volume={original_audio_gain}[bg];"
            f"[1:a]volume={voice_gain}[voice];"
            "[bg][voice]amix=inputs=2:duration=first:dropout_transition=2[mix]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", str(final_video), "-i", str(voice_audio),
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[mix]",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_file),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(final_video), "-i", str(voice_audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_file),
        ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"음성 믹싱 실패: {r.stderr[:240]}")
    log.info("음성 믹싱 완료: %s", out_file)
    return str(out_file)
