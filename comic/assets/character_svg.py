"""
comic/assets/character_svg.py
================================
캐릭터 SVG 시스템 — Max Bullhorn / Baron Bearsworth / The Volatician

3캐릭터 × 5포즈 = 15종 SVG 실루엣
플랫 디자인, CSS 애니메이션 가능, 400×600px 기준

사용법:
    from comic.assets.character_svg import get_character_svg
    svg = get_character_svg("max", "attack")   # SVG 문자열 반환
    svg = get_character_svg("baron", "idle")
    svg = get_character_svg("vol", "chaos")
"""

# ── 색상 팔레트 ──
COLORS = {
    "max":   {"primary": "#10b981", "secondary": "#fbbf24", "accent": "#065f46", "glow": "#34d39933"},
    "baron": {"primary": "#ef4444", "secondary": "#1a1a2e", "accent": "#7f1d1d", "glow": "#ef444433"},
    "vol":   {"primary": "#7c3aed", "secondary": "#a78bfa", "accent": "#4c1d95", "glow": "#7c3aed33"},
}

def get_character_svg(character: str, pose: str = "idle", size: int = 200) -> str:
    """
    캐릭터 SVG 문자열 반환.

    Args:
        character: "max" | "baron" | "vol"
        pose: "idle" | "attack" | "defend" | "victory" | "defeat"
        size: SVG 크기 (기본 200px)

    Returns:
        SVG 문자열 (HTML 내장용)
    """
    generators = {
        "max": _max_svg,
        "baron": _baron_svg,
        "vol": _vol_svg,
    }
    gen = generators.get(character, _max_svg)
    return gen(pose, size)


def get_character_emoji(character: str) -> str:
    """캐릭터 이모지 반환"""
    return {"max": "🐂", "baron": "🐻", "vol": "⚡"}.get(character, "🐂")


def get_character_name(character: str) -> str:
    """캐릭터 정식 이름"""
    return {
        "max": "Max Bullhorn",
        "baron": "Baron Bearsworth",
        "vol": "The Volatician",
    }.get(character, "Max Bullhorn")


# ──────────────────────────────────────────────────────────────
# Max Bullhorn — 황금 황소 전사
# ──────────────────────────────────────────────────────────────

def _max_svg(pose: str, size: int) -> str:
    c = COLORS["max"]
    p, s, a = c["primary"], c["secondary"], c["accent"]

    # 포즈별 변형
    poses = {
        "idle": {"body_rotate": "0", "arm_angle": "-10", "horn_glow": "0"},
        "attack": {"body_rotate": "-5", "arm_angle": "-45", "horn_glow": "1"},
        "defend": {"body_rotate": "5", "arm_angle": "20", "horn_glow": "0"},
        "victory": {"body_rotate": "0", "arm_angle": "-60", "horn_glow": "1"},
        "defeat": {"body_rotate": "10", "arm_angle": "40", "horn_glow": "0"},
    }
    pp = poses.get(pose, poses["idle"])

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300" width="{size}" height="{int(size*1.5)}">
  <defs>
    <radialGradient id="mg" cx="50%" cy="40%"><stop offset="0%" stop-color="{s}"/><stop offset="100%" stop-color="{p}"/></radialGradient>
    <filter id="mgl"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <g transform="rotate({pp['body_rotate']}, 100, 150)">
    <!-- 몸체 -->
    <ellipse cx="100" cy="180" rx="50" ry="65" fill="url(#mg)" stroke="{a}" stroke-width="2"/>
    <!-- 머리 -->
    <circle cx="100" cy="100" r="40" fill="{p}" stroke="{a}" stroke-width="2"/>
    <!-- 뿔 좌 -->
    <path d="M70,85 Q55,50 65,30" stroke="{s}" stroke-width="6" fill="none" stroke-linecap="round" {('filter="url(#mgl)"' if pp['horn_glow']=='1' else '')}/>
    <!-- 뿔 우 -->
    <path d="M130,85 Q145,50 135,30" stroke="{s}" stroke-width="6" fill="none" stroke-linecap="round" {('filter="url(#mgl)"' if pp['horn_glow']=='1' else '')}/>
    <!-- 눈 -->
    <circle cx="85" cy="95" r="5" fill="#fff"/><circle cx="85" cy="95" r="3" fill="{a}"/>
    <circle cx="115" cy="95" r="5" fill="#fff"/><circle cx="115" cy="95" r="3" fill="{a}"/>
    <!-- 코 -->
    <ellipse cx="100" cy="112" rx="12" ry="8" fill="{a}" opacity="0.6"/>
    <!-- 팔 -->
    <line x1="55" y1="160" x2="25" y2="{180+int(pp['arm_angle'])}" stroke="{p}" stroke-width="10" stroke-linecap="round"/>
    <line x1="145" y1="160" x2="175" y2="{180+int(pp['arm_angle'])}" stroke="{p}" stroke-width="10" stroke-linecap="round"/>
    <!-- 다리 -->
    <line x1="80" y1="235" x2="75" y2="280" stroke="{p}" stroke-width="12" stroke-linecap="round"/>
    <line x1="120" y1="235" x2="125" y2="280" stroke="{p}" stroke-width="12" stroke-linecap="round"/>
    <!-- 갑옷 장식 -->
    <path d="M80,155 L100,140 L120,155" stroke="{s}" stroke-width="3" fill="none"/>
    <circle cx="100" cy="170" r="8" fill="{s}" opacity="0.8"/>
  </g>
</svg>'''


# ──────────────────────────────────────────────────────────────
# Baron Bearsworth — 어둠의 곰 악당
# ──────────────────────────────────────────────────────────────

def _baron_svg(pose: str, size: int) -> str:
    c = COLORS["baron"]
    p, s, a = c["primary"], c["secondary"], c["accent"]

    poses = {
        "idle": {"body_rotate": "0", "claw_show": "0", "eye_glow": "0"},
        "attack": {"body_rotate": "-8", "claw_show": "1", "eye_glow": "1"},
        "defend": {"body_rotate": "5", "claw_show": "0", "eye_glow": "0"},
        "victory": {"body_rotate": "-3", "claw_show": "1", "eye_glow": "1"},
        "defeat": {"body_rotate": "12", "claw_show": "0", "eye_glow": "0"},
    }
    pp = poses.get(pose, poses["idle"])
    claw_opacity = "1" if pp["claw_show"] == "1" else "0.3"

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300" width="{size}" height="{int(size*1.5)}">
  <defs>
    <radialGradient id="bg" cx="50%" cy="40%"><stop offset="0%" stop-color="#333"/><stop offset="100%" stop-color="{s}"/></radialGradient>
    <filter id="bgl"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <g transform="rotate({pp['body_rotate']}, 100, 150)">
    <!-- 몸체 -->
    <ellipse cx="100" cy="175" rx="55" ry="70" fill="url(#bg)" stroke="{a}" stroke-width="2"/>
    <!-- 머리 -->
    <circle cx="100" cy="95" r="42" fill="{s}" stroke="{a}" stroke-width="2"/>
    <!-- 귀 -->
    <circle cx="65" cy="65" r="14" fill="#333" stroke="{a}" stroke-width="1.5"/>
    <circle cx="135" cy="65" r="14" fill="#333" stroke="{a}" stroke-width="1.5"/>
    <!-- 눈 (공격 시 빨간 발광) -->
    <circle cx="82" cy="90" r="6" fill="{p}" {('filter="url(#bgl)"' if pp['eye_glow']=='1' else '')}/>
    <circle cx="118" cy="90" r="6" fill="{p}" {('filter="url(#bgl)"' if pp['eye_glow']=='1' else '')}/>
    <circle cx="82" cy="90" r="3" fill="#000"/>
    <circle cx="118" cy="90" r="3" fill="#000"/>
    <!-- 주둥이 -->
    <ellipse cx="100" cy="108" rx="15" ry="10" fill="#444"/>
    <path d="M92,112 L100,118 L108,112" stroke="{p}" stroke-width="1.5" fill="none"/>
    <!-- 모자 (탑햇) -->
    <rect x="72" y="42" width="56" height="8" rx="2" fill="#111"/>
    <rect x="80" y="15" width="40" height="30" rx="4" fill="#111" stroke="#333" stroke-width="1"/>
    <line x1="80" y1="35" x2="120" y2="35" stroke="{p}" stroke-width="2"/>
    <!-- 팔 + 발톱 -->
    <line x1="50" y1="155" x2="20" y2="180" stroke="#333" stroke-width="11" stroke-linecap="round"/>
    <line x1="150" y1="155" x2="180" y2="180" stroke="#333" stroke-width="11" stroke-linecap="round"/>
    <!-- 발톱 -->
    <g opacity="{claw_opacity}">
      <line x1="15" y1="178" x2="5" y2="190" stroke="{p}" stroke-width="3" stroke-linecap="round"/>
      <line x1="20" y1="182" x2="12" y2="196" stroke="{p}" stroke-width="3" stroke-linecap="round"/>
      <line x1="25" y1="184" x2="19" y2="198" stroke="{p}" stroke-width="3" stroke-linecap="round"/>
      <line x1="175" y1="178" x2="185" y2="190" stroke="{p}" stroke-width="3" stroke-linecap="round"/>
      <line x1="180" y1="182" x2="188" y2="196" stroke="{p}" stroke-width="3" stroke-linecap="round"/>
      <line x1="185" y1="184" x2="191" y2="198" stroke="{p}" stroke-width="3" stroke-linecap="round"/>
    </g>
    <!-- 다리 -->
    <line x1="78" y1="235" x2="72" y2="282" stroke="#333" stroke-width="14" stroke-linecap="round"/>
    <line x1="122" y1="235" x2="128" y2="282" stroke="#333" stroke-width="14" stroke-linecap="round"/>
    <!-- 망토 -->
    <path d="M50,140 Q40,200 55,260 L100,240 L145,260 Q160,200 150,140" fill="{a}" opacity="0.4"/>
  </g>
</svg>'''


# ──────────────────────────────────────────────────────────────
# The Volatician — 혼돈의 마법사
# ──────────────────────────────────────────────────────────────

def _vol_svg(pose: str, size: int) -> str:
    c = COLORS["vol"]
    p, s, a = c["primary"], c["secondary"], c["accent"]

    poses = {
        "idle": {"float_y": "0", "orb_glow": "0", "robe_wave": "0"},
        "cast": {"float_y": "-10", "orb_glow": "1", "robe_wave": "5"},
        "reveal": {"float_y": "-5", "orb_glow": "1", "robe_wave": "3"},
        "chaos": {"float_y": "-15", "orb_glow": "1", "robe_wave": "8"},
        "idle": {"float_y": "0", "orb_glow": "0", "robe_wave": "0"},
    }
    pp = poses.get(pose, poses["idle"])
    orb_filter = 'filter="url(#vgl)"' if pp["orb_glow"] == "1" else ""

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300" width="{size}" height="{int(size*1.5)}">
  <defs>
    <radialGradient id="vg" cx="50%" cy="30%"><stop offset="0%" stop-color="{s}"/><stop offset="100%" stop-color="{a}"/></radialGradient>
    <filter id="vgl"><feGaussianBlur stdDeviation="6" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    <linearGradient id="robe" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{a}"/><stop offset="100%" stop-color="#0a0a1a"/></linearGradient>
  </defs>
  <g transform="translate(0,{pp['float_y']})">
    <!-- 로브 -->
    <path d="M60,120 Q50,200 40,290 L160,290 Q150,200 140,120 Z" fill="url(#robe)" stroke="{p}" stroke-width="1" opacity="0.9"/>
    <!-- 로브 물결 -->
    <path d="M40,290 Q70,{280-int(pp['robe_wave'])} 100,290 Q130,{280+int(pp['robe_wave'])} 160,290" fill="none" stroke="{s}" stroke-width="1.5" opacity="0.5"/>
    <!-- 후드 -->
    <path d="M65,125 Q100,70 135,125 Q120,110 100,108 Q80,110 65,125" fill="{a}" stroke="{p}" stroke-width="1.5"/>
    <!-- VIX 마스크 (얼굴) -->
    <rect x="78" y="95" width="44" height="25" rx="5" fill="#000" stroke="{p}" stroke-width="2" {orb_filter}/>
    <text x="100" y="114" text-anchor="middle" fill="{s}" font-family="monospace" font-size="16" font-weight="bold">VIX</text>
    <!-- 눈 (발광) -->
    <circle cx="88" cy="92" r="4" fill="{s}" opacity="0.8" {orb_filter}/>
    <circle cx="112" cy="92" r="4" fill="{s}" opacity="0.8" {orb_filter}/>
    <!-- 오브 (마법구) -->
    <circle cx="45" cy="180" r="15" fill="{p}" opacity="0.6" {orb_filter}/>
    <circle cx="45" cy="180" r="8" fill="{s}" opacity="0.8"/>
    <circle cx="155" cy="180" r="15" fill="{p}" opacity="0.6" {orb_filter}/>
    <circle cx="155" cy="180" r="8" fill="{s}" opacity="0.8"/>
    <!-- 번개 이펙트 (cast/chaos 시) -->
    {'<path d="M45,165 L30,140 L50,150 L35,120" stroke="' + s + '" stroke-width="2" fill="none" opacity="0.7"/>' if pp['orb_glow']=='1' else ''}
    {'<path d="M155,165 L170,140 L150,150 L165,120" stroke="' + s + '" stroke-width="2" fill="none" opacity="0.7"/>' if pp['orb_glow']=='1' else ''}
    <!-- 부유 이펙트 -->
    <ellipse cx="100" cy="295" rx="40" ry="5" fill="{p}" opacity="0.2"/>
    <!-- 차트 파편 이펙트 -->
    {'<path d="M30,250 L50,230 L70,245 L90,220" stroke="' + p + '" stroke-width="1.5" fill="none" opacity="0.4"/>' if pp['orb_glow']=='1' else ''}
    {'<path d="M110,240 L130,225 L150,235 L170,210" stroke="' + p + '" stroke-width="1.5" fill="none" opacity="0.4"/>' if pp['orb_glow']=='1' else ''}
  </g>
</svg>'''


# ──────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────

def get_all_characters() -> list:
    """전체 캐릭터 목록"""
    return ["max", "baron", "vol"]


def get_all_poses(character: str) -> list:
    """캐릭터별 포즈 목록"""
    if character == "vol":
        return ["idle", "cast", "reveal", "chaos"]
    return ["idle", "attack", "defend", "victory", "defeat"]


def get_character_for_regime(regime: str) -> str:
    """레짐 기반 주도 캐릭터 반환"""
    regime_map = {
        "Risk-On": "max",
        "Risk-Off": "baron",
        "Oil Shock": "baron",
        "Transition": "max",
        "Stagflation Risk": "vol",
    }
    return regime_map.get(regime, "max")


def get_pose_for_context(character: str, regime: str, risk_level: str) -> str:
    """레짐 + 리스크 기반 포즈 자동 선택"""
    if character == "vol":
        return "chaos" if risk_level == "HIGH" else "cast"

    if character == "max":
        if regime in ("Risk-On",):
            return "victory"
        if regime in ("Risk-Off", "Oil Shock"):
            return "defend"
        return "idle"

    if character == "baron":
        if regime in ("Risk-Off", "Oil Shock"):
            return "attack"
        if regime in ("Risk-On",):
            return "defeat"
        return "idle"

    return "idle"
