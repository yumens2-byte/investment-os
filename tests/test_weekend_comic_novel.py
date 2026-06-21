import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_novelify_episodes_uses_active_claude_model(monkeypatch):
    from weekend import comic_novel

    calls = []

    class MockMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text=("충분한 소설 본문입니다. " * 80))]
            )

    class MockAnthropic:
        def __init__(self):
            self.messages = MockMessages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        types.SimpleNamespace(Anthropic=MockAnthropic),
    )
    monkeypatch.setattr(comic_novel, "CLAUDE_NOVEL_MODEL", "claude-sonnet-4-6")

    result = comic_novel.novelify_episodes(
        [
            {
                "episode_no": 102,
                "title": "테스트 에피소드",
                "content": "EDT가 시장의 균열을 발견했다. " * 20,
            }
        ]
    )

    assert result["success"] is True
    assert calls
    assert calls[0]["model"] == "claude-sonnet-4-6"
