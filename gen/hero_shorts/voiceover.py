"""
gen/hero_shorts/voiceover.py — 대사 계획·TTS·오디오 믹싱
==================================================
비용 정책:
    - backend='dummy' 는 0원 테스트 경로
    - backend='gsk'   는 실제 TTS 경로 (명시 호출 시만)

역할:
    1) 컷계획 JSON → 컷별 대사 계획 JSON (v2.8.0: 라인 단위 role+speaker)
    2) 대사 계획 → 컷별 음성 WAV + 병합 WAV (v2.8.0: 2인 네이티브 대사 지원)
    3) 최종 영상 + 병합 WAV (+BGM) → 발행용 혼합 MP4 (v2.8.0: 덕킹 믹스)
"""
import json
import os
import re
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

# v2.8.0 — 캐릭터 단상(窓談) 템플릿: 감정 태그 포함, 투자 권유 없는 무드 대사만
CHARACTER_LINES = {
    "ESTABLISH_THREAT": "[alarmed] 여긴 다 침수야!",
    "ESCALATE": "[nervously] 흐름이 또 흔들려…",
    "ENGAGE": "[curious] 이번엔 어디를 먼저 고치지?",
    "CRISIS": "[alarmed] 연쇄로 넘어가기 직전이야!",
    "TURN": "[excited] 자원 재배분, 지금이다!",
    "RESOLVE": "[reassuring] 수면 위로 돌아오고 있어.",
    "AFTERMATH_CALM": "[sympathetic] 자리에 남은 균열이 보여.",
    "REGROUP": "[professional] 다음 우선순위를 정리하자.",
    "NEXT_OMEN": "[whispers] 저거… 또 시작인가?",
    "FB_OPEN": "[curious] 그때 무슨 일이 있었던 거야?",
    "FB_BODY": "[sympathetic] 숫자보다 선택이 크게 남았겠네.",
    "FB_PEAK": "[dramatically] 그 한 번은 기준이 됐어.",
    "FB_RETURN": "[professional] 그래서 지금 기준이 중요한 거야.",
}


def _safe_title(title: str) -> str:
    return str(title or "이 회차")


def narrator_speaker() -> str:
    """내레이션 화자 — env HERO_TTS_SPEAKER, 미지정 시 Austin(v2.8.0 기본, 묵직한 저음 실측 91Hz)."""
    return os.environ.get("HERO_TTS_SPEAKER") or "Austin"


def parse_cast(mapping: Optional[str] = None) -> Dict[str, str]:
    """HERO_TTS_CAST 형식 'CHAR1=Eve,CHAR2=Terry' → {역할: 화자}. None이면 env 조회."""
    raw = os.environ.get("HERO_TTS_CAST", "") if mapping is None else mapping
    cast = {}
    for part in str(raw or "").split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                cast[k.strip()] = v.strip()
    return cast


def speaker_for_line(role: str, cast: Optional[Dict[str, str]] = None) -> Optional[str]:
    cast = parse_cast() if cast is None else cast
    if role == "NARRATOR":
        return narrator_speaker()
    return cast.get(role)


def line_for_role(role: str, title: str, episode_type: str, outcome: str) -> str:
    template = ROLE_LINES.get(role, "상황은 계속 변하고, 판단 기준은 더 정교해져야 한다.")
    text = template.format(title=_safe_title(title), type=episode_type or "UNKNOWN", outcome=outcome or "UNKNOWN")
    return " ".join(text.split())


def estimate_tts_duration(text: str, max_duration_sec: float, chars_per_sec: float = 6.5) -> float:
    base = max(2.0, len(text.strip()) / chars_per_sec + 0.8)
    return round(min(max_duration_sec, base), 2)


def build_lines_for_cut(cut: Dict, title: str, episode_type: str, outcome: str) -> List[Dict]:
    """v2.8.0 — 컷 1개의 라인 배열 생성.
    우선순위: ① plan JSON 이 dialogue.lines 명시 ② env HERO_TTS_DIALOGUE=1 이면 내레이션+캐릭터 단상 ③ 내레이션 단독.
    명시 캐릭터 라인에 화자 배정이 없으면 fail-closed로 즉시 거부한다(Ep87 교훈)."""
    explicit = (cut.get("dialogue") or {}).get("lines") or []
    cast = parse_cast()
    lines: List[Dict] = []
    if explicit:
        for ln in explicit:
            role = str(ln.get("role") or "NARRATOR")
            text = str(ln.get("text") or "").strip()
            if not text:
                raise ValueError(f"cut{cut.get('cut_no')} 대사 라인 텍스트 공란 — 생성 거부")
            speaker = ln.get("speaker") or speaker_for_line(role, cast)
            if not speaker:
                raise ValueError(
                    f"cut{cut.get('cut_no')} 캐릭터 화자 미설정: {role} — HERO_TTS_CAST(예: CHAR1=Eve) 필요(fail-closed)"
                )
            lines.append({"role": role, "text": text, "speaker": speaker})
        return lines
    narrator_text = line_for_role(cut.get("role", ""), title, episode_type, outcome)
    if os.environ.get("HERO_TTS_DIALOGUE") == "1":
        quip = CHARACTER_LINES.get(cut.get("role", ""))
        speaker = speaker_for_line("CHAR1", cast)
        if quip and speaker:
            lines.append({"role": "NARRATOR", "text": narrator_text, "speaker": narrator_speaker()})
            lines.append({"role": "CHAR1", "text": quip, "speaker": speaker})
            return lines
        log.warning("캐릭터 단상 생략 — ROLE=%s 단상 템플릿 또는 HERO_TTS_CAST 부재", cut.get("role"))
    lines.append({"role": "NARRATOR", "text": narrator_text, "speaker": narrator_speaker()})
    return lines


def build_dialogue_plan(plan: Dict) -> Dict:
    cuts = []
    title = plan.get("title", "")
    episode_type = plan.get("type", "")
    outcome = plan.get("outcome", "")
    for cut in plan.get("cuts", []):
        request = cut.get("request", {})
        max_duration = float(request.get("duration", 10))
        lines = build_lines_for_cut(cut, title, episode_type, outcome)
        text = " ".join(ln["text"] for ln in lines)
        cuts.append({
            "cut_no": cut.get("cut_no"),
            "role": cut.get("role"),
            "text": text,
            "lines": lines,
            "target_duration_sec": estimate_tts_duration(text, max_duration),
            "max_duration_sec": max_duration,
        })
    payload = {
        "episode": plan.get("episode"),
        "title": title,
        "type": episode_type,
        "outcome": outcome,
        "narrator_speaker": narrator_speaker(),
        "cast": parse_cast(),
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


def _dummy_tts(text: str, out_path, duration_sec: float, freq_offset: int = 0):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    freq = 520 + freq_offset + (sum(ord(ch) for ch in text) % 180)
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


def _gsk_generate(cmd: List[str], out_path, kind: str = "TTS") -> str:
    """v2.7.3 fail-closed 공통화: 오류도 exit 0+ok 봉투로 반환되는 gsk 특성 방어.
    ① 종료코드 검사 ② 파일 부재 시 응답 본문 검사(거부 명시 검출) ③ URL 폴백 ④ 최종 무파일 거부."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gsk {kind} 실패: {(r.stderr or r.stdout)[:300]}")
    if not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
        body = r.stdout or ""
        try:
            _d = json.loads(body)
            if str(_d.get("status")) != "ok" or "Invalid params" in str(_d.get("data", {}).get("result", "")):
                raise RuntimeError(f"gsk {kind} 거부: {str(_d.get('data', {}).get('result'))[:200]}")
            _data = _d.get("data") or {}
            audio_url = (_data.get("audio_url") or _data.get("url") or _data.get("audio")
                         or (_data.get("audio_urls") or [None])[0])
        except RuntimeError:
            raise
        except Exception:
            audio_url = None
        if not audio_url:
            _m = re.search(r"https://[^\s\"']+", body)
            audio_url = _m.group(0) if _m else None
        if audio_url:
            import urllib.request as _uq
            _uq.urlretrieve(audio_url, str(out_path))
    if not Path(out_path).exists() or Path(out_path).stat().st_size == 0:
        raise RuntimeError(f"gsk {kind} 출력 부재(응답): {r.stdout[:200]}")
    return str(out_path)


def _gsk_tts(text: str, out_path, model: Optional[str] = None, speaker: Optional[str] = None,
             tier: Optional[str] = None, stability: Optional[float] = None,
             speakers: Optional[List[Dict]] = None, prompt_text: Optional[str] = None):
    """v2.8.0 — v4-tts 호출 확장.
    단일 화자: params {"speaker": ...} / 2인 대사: params {"speakers": [{speaker, voice_name}, ...]}.
    tier(standard|turbo)·stability는 env HERO_TTS_TIER / HERO_TTS_STABILITY로도 지정 가능."""
    chosen_model = model or "elevenlabs/v4-tts"
    params: Dict = {}
    if speakers:
        if not (1 <= len(speakers) <= 2):
            raise RuntimeError(f"v4-tts 대사 화자 수 제한 위반: {len(speakers)}인 — 2인 이하 필요(fail-closed)")
        params["speakers"] = speakers
    else:
        speaker = speaker or (os.environ.get("HERO_TTS_SPEAKER") or None)
        if not speaker:
            raise RuntimeError("TTS 화자 미지정 — HERO_TTS_SPEAKER 또는 --speaker 필요(v2.7.3 fail-closed)")
        params["speaker"] = speaker
    tier = tier or (os.environ.get("HERO_TTS_TIER") or None)
    if tier:
        params["tier"] = tier
    stability = stability if stability is not None else os.environ.get("HERO_TTS_STABILITY")
    if stability is not None and str(stability).strip() != "":
        params["stability"] = float(stability)
    cmd = ["gsk", "audio_generation", prompt_text or text, "-m", chosen_model, "-o", str(out_path),
           "-p", json.dumps(params)]
    return _gsk_generate(cmd, out_path, kind="TTS")


def _gsk_tts_dialogue(lines: List[Dict], out_path, model: Optional[str] = None, tier: Optional[str] = None) -> str:
    """v2.8.0 — 2인 네이티브 대사 1회 호출: 'Speaker1: ...\nSpeaker2: ...' 형식(v4-tts 스펙)."""
    if not (1 <= len(lines) <= 2):
        raise RuntimeError(f"네이티브 대사는 2라인 이하만 지원: {len(lines)}라인 — 초과 시 라인별 생성+병합 필요")
    speakers = [{"speaker": f"Speaker{i+1}", "voice_name": ln["speaker"]} for i, ln in enumerate(lines)]
    prompt_text = "\n".join(f"Speaker{i+1}: {ln['text']}" for i, ln in enumerate(lines))
    return _gsk_tts("", out_path, model=model, tier=tier, speakers=speakers, prompt_text=prompt_text)


def generate_bgm(out_path, duration_sec: float, prompt: Optional[str] = None, model: Optional[str] = None) -> str:
    """v2.8.0 — 배경음악 생성(elevenlabs/music). 보컬 기본값이라 'instrumental only'를 강제 주입한다.
    실패 시 fail-closed(RuntimeError) — 무음/스테일로 마스킹되지 않는다(Ep87 교훈)."""
    prompt = prompt or os.environ.get("HERO_BGM_PROMPT") or ""
    if not prompt.strip():
        raise RuntimeError("BGM 프롬프트 미지정 — HERO_BGM_PROMPT 필요(fail-closed)")
    if "instrumental" not in prompt.lower():
        prompt = prompt + ", instrumental only"
    duration = max(10, min(300, int(float(duration_sec))))
    cmd = ["gsk", "audio_generation", prompt, "-m", model or "elevenlabs/music",
           "-p", json.dumps({"duration": duration}), "-o", str(out_path)]
    return _gsk_generate(cmd, out_path, kind="BGM")


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
    tier = os.environ.get("HERO_TTS_TIER") or None
    stability = os.environ.get("HERO_TTS_STABILITY")
    clips = []
    for cut in payload.get("cuts", []):
        cut_no = cut["cut_no"]
        raw_clip_path = out_dir / f"{episode}_voice_cut{cut_no}_raw.wav"
        clip_path = out_dir / f"{episode}_voice_cut{cut_no}.wav"
        lines = cut.get("lines") or [{"role": "NARRATOR", "text": cut["text"], "speaker": speaker or narrator_speaker()}]
        if raw_clip_path.exists():
            raw_clip_path.unlink()  # v2.7.3: 이전 실행 raw 재사용 금지 — Ep87 삐 결함 원인(더미 사인 잔존)
        target_duration = float(cut.get("target_duration_sec", 3.0))
        segment_duration = float(cut.get("max_duration_sec", target_duration))
        if backend == "dummy":
            if len(lines) == 1:
                result = _dummy_tts(lines[0]["text"], raw_clip_path, target_duration)
            else:
                parts = []
                for i, ln in enumerate(lines):
                    p = out_dir / f"{episode}_voice_cut{cut_no}_line{i}.wav"
                    _dummy_tts(ln["text"], p, target_duration / len(lines), freq_offset=i * 90)
                    parts.append(str(p))
                concat_audio(parts, raw_clip_path)
                result = str(raw_clip_path)
        elif backend == "gsk":
            if len(lines) == 1:
                result = _gsk_tts(lines[0]["text"], raw_clip_path, model=model,
                                  speaker=speaker or lines[0]["speaker"], tier=tier, stability=stability)
            elif len(lines) == 2:
                result = _gsk_tts_dialogue(lines, raw_clip_path, model=model, tier=tier)
            else:
                parts = []
                for i, ln in enumerate(lines):
                    p = out_dir / f"{episode}_voice_cut{cut_no}_line{i}.wav"
                    _gsk_tts(ln["text"], p, model=model, speaker=ln["speaker"], tier=tier, stability=stability)
                    parts.append(str(p))
                concat_audio(parts, raw_clip_path)
                result = str(raw_clip_path)
        else:
            raise ValueError(f"알 수 없는 TTS 백엔드: {backend}")
        fitted = fit_audio_duration(result, clip_path, segment_duration)
        clips.append({
            "cut_no": cut_no,
            "role": cut.get("role"),
            "text": " ".join(ln["text"] for ln in lines),
            "lines": lines,
            "audio_file": fitted,
            "raw_audio_file": result,
            "duration_sec": round(probe_duration(fitted), 2),
            "target_duration_sec": target_duration,
            "segment_duration_sec": segment_duration,
        })
    merged = out_dir / f"{episode}_voice_merged.wav"
    concat_audio([c["audio_file"] for c in clips], merged)
    merged_duration = round(probe_duration(merged), 2)
    bgm_file = None
    bgm_prompt = os.environ.get("HERO_BGM_PROMPT") or ""
    if backend == "gsk" and bgm_prompt.strip() and os.environ.get("HERO_BGM_DISABLED") != "1":
        bgm_file = generate_bgm(out_dir / f"{episode}_bgm.mp3", merged_duration, prompt=bgm_prompt)
    result = {
        "episode": payload.get("episode"),
        "backend": backend,
        "model": model,
        "speaker": speaker,
        "narrator_speaker": narrator_speaker(),
        "cast": parse_cast(),
        "tier": tier,
        "clips": clips,
        "merged_audio": str(merged),
        "merged_duration_sec": merged_duration,
        "bgm_file": bgm_file,
    }
    manifest = out_dir / f"{episode}_voice_manifest.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("TTS 완료: ep=%s clips=%d merged=%s bgm=%s", payload.get("episode"), len(clips), merged, bgm_file)
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


def audio_rms_db(path) -> Optional[float]:
    """ffmpeg astats 기반 전체 RMS(dB) — 무음/과소음 판별용(QC v2.8.0)."""
    r = subprocess.run([
        "ffmpeg", "-i", str(path), "-af", "astats=metadata=0", "-f", "null", "-"
    ], capture_output=True, text=True)
    vals = re.findall(r"RMS level(?: dB)?:\s*(-?[\d.]+|-?inf)", r.stderr or "")
    if not vals:
        return None
    try:
        return float(vals[-1])
    except ValueError:
        return None  # -inf 등 비수치 — 무음 처리


def verify_audio_outputs(path, min_rms_db: float = -45.0) -> Dict:
    """QC 확장(v2.8.0) — 발행본 오디오 가청성 재확인. 무음(사인 결함 등)은 경고로 즉시 노출."""
    try:
        db = audio_rms_db(path)
    except Exception as exc:
        return {"path": str(path), "audible": None, "warning": f"RMS 측정 실패: {exc}"}
    audible = db is not None and db > min_rms_db
    if not audible:
        log.warning("오디오 가청성 경고: %s rms=%sdB (기준 %sdB 초과 필요)", path, db, min_rms_db)
    return {"path": str(path), "rms_db": db, "audible": audible}


def _ducking_filter(voice_gain: float, original_audio_gain: float, with_orig: bool) -> str:
    """v2.8.0 덕킹 체인 — BGM을 내레이션에 사이드체인 압축해 말이 겹칠 때만 낮춘다.
    BGM 사용 시 원본 컷 효과음 트랙(orig)은 그대로 유지한다(Ep86~ 이펙트 보존)."""
    chains = [
        f"[1:a]volume={voice_gain}[voice]",
        "[2:a]volume=0.5[bgraw]",
        "[bgraw][1:a]sidechaincompress=threshold=0.02:ratio=8:attack=60:release=480[duck]",
        "[duck]volume=0.32[bg]",
    ]
    if with_orig:
        chains.append(f"[0:a]volume={original_audio_gain}[orig]")
        chains.append("[bg][voice][orig]amix=inputs=3:duration=first:dropout_transition=2:normalize=0[mix]")
    else:
        chains.append("[bg][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mix]")
    return ";".join(chains)


def mix_voice_over(final_video, voice_audio, out_file, original_audio_gain=0.22, voice_gain=1.5,
                   bgm_audio=None) -> str:
    final_video = Path(final_video)
    voice_audio = Path(voice_audio)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    if bgm_audio:
        bgm_audio = Path(bgm_audio)
        if not bgm_audio.exists() or bgm_audio.stat().st_size == 0:
            raise RuntimeError(f"BGM 파일 부재: {bgm_audio} (fail-closed)")
        with_orig = has_audio_stream(final_video)
        filter_complex = _ducking_filter(voice_gain, original_audio_gain, with_orig)
        cmd = [
            "ffmpeg", "-y", "-i", str(final_video), "-i", str(voice_audio), "-i", str(bgm_audio),
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[mix]",
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(out_file),
        ]
    elif has_audio_stream(final_video):
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
    try:
        verify_audio_outputs(out_file)
    except Exception as exc:  # QC 확장은 경고 전용 — 믹싱 자체는 성공으로 유지
        log.warning("오디오 검증 건너뜀: %s", exc)
    log.info("음성 믹싱 완료: %s (bgm=%s)", out_file, bool(bgm_audio))
    return str(out_file)
