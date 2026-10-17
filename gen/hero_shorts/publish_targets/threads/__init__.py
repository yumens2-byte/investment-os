"""
gen/hero_shorts/publish_targets/threads/ — 쓰레드 타깃 (W6)
===========================================================
설계서 §2.2 · §4.4. 영상 1건만 다룬다(텍스트 단독·이미지·캐러셀은 범위 밖).
"""
from typing import Any, Dict, Optional

from ..base import (
    STATUS_BLOCKED,
    TargetPlan,
    media_facts,
    resolve_account_id,
)
from . import gates as gates_mod
from . import payload as payload_mod
from . import verify as verify_mod

VERSION = "1.0.0"

ENV_ACCOUNT_KEY = "HERO_THREADS_ACCOUNT_ID"


class ThreadsTarget:
    """쓰레드 영상 1건 타깃."""

    platform = "threads"
    env_account_key = ENV_ACCOUNT_KEY

    def __init__(self, account_id: Optional[str] = None, *,
                 first_comment: Optional[str] = None,
                 recent_post_count_24h: int = 0,
                 file_bytes: Optional[int] = None,
                 container: str = "mp4",
                 video_codec: str = "h264",
                 audio_codec: str = "aac") -> None:
        self.account_id = account_id
        self.first_comment = first_comment
        self.recent_post_count_24h = recent_post_count_24h
        self.file_bytes = file_bytes
        self.container = container
        self.video_codec = video_codec
        self.audio_codec = audio_codec

    def build_plan(self, record: Dict[str, Any], projection: Dict[str, Any],
                   *, ep_key: Optional[str] = None,
                   common_gate: Optional[Dict[str, Any]] = None) -> TargetPlan:
        ep_key = ep_key or record.get("_ep_key") or ""
        facts = media_facts(record)
        proj = projection or {}

        # TH-G4 파일 크기 — 주입 > 원장 기재 > 로컬 실측. 셋 다 없으면 게이트가 차단한다
        # (fail-closed: 미측정을 통과로 넘기지 않는다 — 설계서 §1.1 G3).
        size = self.file_bytes
        if size is None:
            size = facts.get("size_bytes")
        if size is None:
            size = gates_mod.local_file_bytes(facts.get("video_path"))

        gate = gates_mod.run_threads_gates(
            duration_sec=facts.get("duration_sec"),
            ratio=facts.get("ratio"),
            content=proj.get("content"),
            topic_tag=proj.get("topic_tag"),
            first_comment=self.first_comment,
            file_bytes=size,
            container=self.container,
            video_codec=self.video_codec,
            audio_codec=self.audio_codec,
            recent_post_count_24h=self.recent_post_count_24h,
        )
        merged = _merge_gates(common_gate, gate, own_ids=gates_mod.GATE_IDS)

        account_id = resolve_account_id(self.env_account_key, self.account_id)
        problems = []
        if not account_id:
            problems.append(
                f"{self.env_account_key} 미설정 — 쓰레드 계정 ID 주입 필요(fail-closed)")
        if not merged["ok"]:
            problems.append(f"게이트 미통과: {merged['failed']}")

        payload = {}
        if not problems:
            payload = payload_mod.platform_entry(
                account_id, proj, first_comment=self.first_comment)

        return TargetPlan(
            platform=self.platform,
            account_id=account_id,
            # `ok` = 전송 가능. 게이트 실패든 계정 미설정이든 하나라도 있으면 False.
            gate={**merged, "ok": merged["ok"] and not problems,
                  "blocked": bool(problems), "problems": problems},
            payload=payload,
            idempotency_key=f"{ep_key}:{self.platform}",
        )

    def verify(self, record: Dict[str, Any], result) -> Dict[str, Any]:
        return verify_mod.verify(record, result)

    def extract_id(self, url: str) -> Optional[str]:
        return verify_mod.extract_post_code(url)


def _merge_gates(common_gate, own_gate, *, own_ids) -> Dict[str, Any]:
    common_ok = True if common_gate is None else bool(common_gate.get("ok"))
    common_failed = [] if common_gate is None else list(common_gate.get("failed") or [])
    results = [] if common_gate is None else list(common_gate.get("results") or [])
    ok = common_ok and bool(own_gate.get("ok"))
    return {
        "ok": ok,
        "failed": common_failed + list(own_gate.get("failed") or []),
        "results": results + list(own_gate.get("results") or []),
        "own_gate_ids": list(own_ids),
        "common_ok": common_ok,
    }
