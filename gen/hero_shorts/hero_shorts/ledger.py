"""
gen/hero_shorts/ledger.py — 노션 원장(EDT Arc State Ledger) 파서
=================================================================
역할:
    1. 인덱스 페이지(D-6)의 코드블록을 읽어 정본(정본=Y) page_id 목록 확정.
       → 파이프라인은 절대 page_id 를 하드코딩하지 않는다(ASL-04 규칙).
    2. 정본 페이지를 읽어 ```json 블록의 arc_state 객체를 파싱한다.
    3. 폐기 행(정본=N, 예: Ep90 v1_1)은 목록에서 제외 — 최신 리비전만 사용.

백엔드:
    기본 = `gsk notion read` 서브프로세스 (세션 실행 기준, 크레딧 0).
    NOTION_TOKEN 이 설정되면 공식 REST API 우선 사용(Actions 실행 대응) —
    이 경우 페이지 접근 권한을 integration 에 부여해야 한다.

주의 (실측 2026-09-21):
    - Ep86 `battle_scar` 는 note형, Ep87 은 필드 부재, Ep88+ 는 인물별 구조
      → 소비자(cutplanner 등)는 battle_scar 를 옵셔널로 다룬다.
    - `act` 필드는 Ep88 부터 존재 → 옵셔널.
    - `next_episode.expected_type_hint` 는 Ep89 부터 기재 → 부재 시 유형은
      v1.7 기본값 규칙(BATTLE) 대체.
"""
import json
import logging
import os
import re
import subprocess

log = logging.getLogger("hero_shorts.ledger")

# 원장 인덱스 page_id (D-6 조회 인덱스 — 삭제 금지, 행 추가·갱신만 허용)
INDEX_PAGE_ID = "3de9208cbdc3816f9c97e17ed21d2ec0"


def _read_page_notion_api(page_id, token):
    """공식 Notion REST API 백엔드 (Actions 실행 대응).

    아직 운영 경로는 아니며(토큰 부재) — 존재 시 우선 사용하도록 설계만 포함.
    """
    import urllib.request
    req = urllib.request.Request(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers={"Authorization": f"Bearer {token}",
                 "Notion-Version": "2022-06-28"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.dumps(json.load(r), ensure_ascii=False)
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"노션 API 호출 실패(HTTP {e.code}) — NOTION_TOKEN 만료·페이지 접근 권한 확인"
        )


def read_page_via_gsk(page_id):
    """gsk CLI 백엔드 — 크레딧 0. 페이지 본문 JSON(envelope)을 문자열로 반환."""
    cmd = ["gsk", "notion", "read", "--page_id", page_id]
    log.debug("gsk 노션 읽기 실행: %s", page_id)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError(
            "gsk CLI 부재 + NOTION_TOKEN 미설정 — Actions에서 노션 직독은 레포 시크릿 NOTION_TOKEN 등록 필수"
        )
    if out.returncode != 0:
        log.error("gsk notion read 실패(%s): %s", page_id, out.stderr[:200])
        raise RuntimeError(f"gsk notion read 실패: {page_id}")
    return out.stdout


def read_page(page_id):
    """이중 백엔드 진입점 — NOTION_TOKEN 우선, 없으면 gsk CLI."""
    token = os.getenv("NOTION_TOKEN", "")
    if token:
        return _read_page_notion_api(page_id, token)
    return read_page_via_gsk(page_id)


def parse_index(raw_text):
    """인덱스 본문에서 정본 행만 파싱.

    Returns:
        {episode:int: {"page_id":str, "revision":str}} — 정본=Y 행만.
    """
    rows = {}
    # 코드블록 내 `번호|리비전|정본|page_id|hash16` 형식 행 검색
    for m in re.finditer(r"^\s*(\d+)\|([^|]+)\|([YN])\|([0-9a-f]{32})\|", raw_text, re.M):
        ep, rev, canon, pid = int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4)
        if canon != "Y":
            log.info("폐기 행 제외: Ep%d (%s)", ep, rev)
            continue
        rows[ep] = {"page_id": pid, "revision": rev}
    log.info("인덱스 파싱 완료: 정본 %d행 %s", len(rows), sorted(rows))
    return rows


def load_index():
    """인덱스 페이지를 읽어 정본 page_id 사전 반환 (파이프라인 표준 진입)."""
    raw = read_page(INDEX_PAGE_ID)
    d = json.loads(raw)
    content = d["data"]["content"] if isinstance(d, dict) and "data" in d else raw
    return parse_index(content)


def extract_json_obj(page_raw):
    """정본 페이지 본문에서 ```json 블록을 추출해 dict 로 반환."""
    d = json.loads(page_raw)
    content = d["data"]["content"] if isinstance(d, dict) and "data" in d else page_raw
    m = re.search(r"```json\s*(\{.*\})\s*```", content, re.S)
    if not m:
        log.error("JSON 블록 미발견 — page_id 확인 필요")
        raise ValueError("정본 페이지에 ```json 블록 없음")
    return json.loads(m.group(1))


def load_episode(ep_number):
    """회차 번호(예: 86)로 정본 arc_state 객체 로드.

    폐기·재검증 이력이 있는 Ep90 처럼 다중 행이어도 정본=Y 는 1개 —
    parse_index 가 이미 최신 정본만 남긴다.
    """
    idx = load_index()
    if ep_number not in idx:
        log.error("Ep%d 정본 없음 — 원장 미적재(신규 집필 필요)", ep_number)
        raise KeyError(f"Ep{ep_number} 정본 없음")
    raw = read_page(idx[ep_number]["page_id"])
    obj = extract_json_obj(raw)
    log.info("Ep%d 로드 OK: %s", ep_number, obj.get("title"))
    return obj
