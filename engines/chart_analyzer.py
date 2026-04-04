"""
engines/chart_analyzer.py (C-13)
=================================
Gemini Vision 차트 분석

대시보드 PNG 이미지를 Gemini Vision에 입력하여
기술적 분석 콘텐츠 자동 생성.

full/close 세션에서 실행. 유료 TG 채널 전용.
Gemini 실패 시 스킵 (기존 발행에 영향 없음).
"""
import logging

logger = logging.getLogger(__name__)


def analyze_chart(image_path: str, data: dict = None) -> dict:
    """
    대시보드 PNG를 Gemini Vision으로 분석.

    Args:
        image_path: 대시보드 PNG 파일 경로
        data: core_data (레짐/리스크 등 컨텍스트 보강용, 선택)

    Returns:
        {
          "success": True/False,
          "trend": "bullish/bearish/sideways",
          "key_observations": ["관찰 1", "관찰 2"],
          "risk_signals": ["리스크 1"],
          "analysis": "한국어 2~3줄 기술적 분석",
          "telegram": "TG 포맷 텍스트",
        }
    """
    try:
        from core.gemini_gateway import analyze_image, is_available
        if not is_available():
            return {"success": False, "reason": "Gemini 미설정"}

        # 컨텍스트 보강
        regime = ""
        risk = ""
        if data:
            regime = data.get("market_regime", {}).get("market_regime", "")
            risk = data.get("market_regime", {}).get("market_risk_level", "")

        context = ""
        if regime:
            context = f"\n참고: 현재 레짐={regime}, 리스크={risk}"

        prompt = (
            f"이 금융 대시보드 이미지를 분석해줘.{context}\n"
            f"JSON으로 응답:\n"
            f'{{"trend": "bullish/bearish/sideways",\n'
            f' "key_observations": ["관찰 1", "관찰 2", "관찰 3"],\n'
            f' "risk_signals": ["리스크 1"],\n'
            f' "analysis": "한국어 2~3줄 기술적 분석"}}\n\n'
            f"조건:\n"
            f"- 이미지에서 보이는 수치/그래프 패턴만 사용\n"
            f"- 추측 금지, 보이는 데이터만\n"
            f"- key_observations 최대 3개\n"
            f"- analysis는 한국어 2~3줄\n"
            f"- JSON만 출력"
        )

        result = analyze_image(
            image_path=image_path,
            prompt=prompt,
            model="flash-lite",
            response_json=True,
            max_tokens=500,
            temperature=0.3,
        )

        if result.get("success") and result.get("data"):
            d = result["data"]
            trend = str(d.get("trend", "sideways"))
            observations = d.get("key_observations", [])
            risks = d.get("risk_signals", [])
            analysis = str(d.get("analysis", ""))

            if not analysis:
                logger.warning("[ChartAnalyzer] 분석 텍스트 비어있음 → 스킵")
                return {"success": False, "reason": "분석 비어있음"}

            # TG 포맷
            obs_lines = "\n".join(f"  - {o}" for o in observations[:3])
            risk_lines = "\n".join(f"  - {r}" for r in risks[:2])
            regime_label = f" | {regime}" if regime else ""

            telegram = (
                f"📈 <b>AI 차트 분석</b>{regime_label}\n\n"
                f"🔍 트렌드: {trend}\n"
                f"📊 관찰:\n{obs_lines}\n"
                f"⚠️ 리스크:\n{risk_lines}\n\n"
                f"🤖 분석: {analysis}"
            )

            logger.info(
                f"[ChartAnalyzer] 분석 완료 | trend={trend} | "
                f"obs={len(observations)} | {analysis[:30]}..."
            )

            return {
                "success": True,
                "trend": trend,
                "key_observations": observations[:3],
                "risk_signals": risks[:2],
                "analysis": analysis,
                "telegram": telegram,
            }

        logger.warning(f"[ChartAnalyzer] Gemini Vision 실패: {result.get('error', '?')[:50]}")

    except Exception as e:
        logger.warning(f"[ChartAnalyzer] 분석 실패 (무시): {e}")

    return {"success": False, "reason": "Vision 실패"}
