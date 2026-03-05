import json
import pytest
from unittest.mock import MagicMock

from src.config import Config
from src.backman import BackmanService, TAGGING_FEW_SHOT_EXAMPLES


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def mock_client():
    return MagicMock()


@pytest.fixture
def backman(mock_client, config):
    return BackmanService(mock_client, config)


def make_mock_response(text: str) -> MagicMock:
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=text)]
    return mock_response


class TestTagEmotion:
    def test_normal_json_response(self, backman, mock_client):
        """テスト1: 正常なJSONを返すモックで期待通りのdictが返ること"""
        emotion_data = {
            "joy": 0.7, "sadness": 0.0, "anger": 0.0, "fear": 0.5,
            "surprise": 0.3, "disgust": 0.0, "trust": 0.3, "anticipation": 0.6,
            "importance": 0.8, "urgency": 0.3
        }
        payload = json.dumps({"emotion": emotion_data, "scenes": ["work"]})
        mock_client.messages.create.return_value = make_mock_response(payload)

        result = backman.tag_emotion("テスト入力")

        assert result["emotion"]["joy"] == 0.7
        assert result["scenes"] == ["work"]
        mock_client.messages.create.assert_called_once()

    def test_invalid_json_raises_value_error(self, backman, mock_client):
        """テスト2: JSON解析失敗時にValueErrorが発生すること"""
        mock_client.messages.create.return_value = make_mock_response("not valid json")

        with pytest.raises(ValueError, match="Backman returned invalid JSON"):
            backman.tag_emotion("テスト入力")

    def test_incomplete_json_axes_filled(self, backman, mock_client):
        """テスト3: 不完全なJSONでも全10軸が補完されること"""
        # joysのみ含む不完全なemotion
        partial_emotion = {"joy": 0.9}
        payload = json.dumps({"emotion": partial_emotion, "scenes": ["work"]})
        mock_client.messages.create.return_value = make_mock_response(payload)

        result = backman.tag_emotion("テスト入力")

        # 全10軸が存在すること
        all_axes = list(backman.config.EMOTION_AXES) + list(backman.config.META_AXES)
        for ax in all_axes:
            assert ax in result["emotion"], f"Missing axis: {ax}"
        assert result["emotion"]["joy"] == 0.9
        assert result["emotion"]["sadness"] == 0.0

    def test_scenes_capped_at_three(self, backman, mock_client):
        """テスト4: scenesが最大3件に制限されること"""
        emotion_data = {ax: 0.0 for ax in list(backman.config.EMOTION_AXES) + list(backman.config.META_AXES)}
        payload = json.dumps({
            "emotion": emotion_data,
            "scenes": ["work", "learning", "hobby", "health", "daily"]
        })
        mock_client.messages.create.return_value = make_mock_response(payload)

        result = backman.tag_emotion("テスト入力")

        assert len(result["scenes"]) == 3
        assert result["scenes"] == ["work", "learning", "hobby"]

    def test_api_error_propagates(self, backman, mock_client):
        """テスト10: APIエラー時に例外が伝播すること"""
        mock_client.messages.create.side_effect = RuntimeError("API connection error")

        with pytest.raises(RuntimeError, match="API connection error"):
            backman.tag_emotion("テスト入力")


class TestGenerateSummary:
    def test_returns_string_from_mock(self, backman, mock_client):
        """テスト5: モックで文字列が返ること"""
        expected_summary = "ユーザーは仕事でストレスを感じており、上司との関係に不満を持っている。"
        mock_client.messages.create.return_value = make_mock_response(expected_summary)

        turns = [
            {"user_input": "今日も残業だった", "ai_response": "大変でしたね", "timestamp": "2026-03-05T10:00:00"},
        ]
        result = backman.generate_summary(turns)

        assert result == expected_summary
        mock_client.messages.create.assert_called_once()

    def test_empty_turns_returns_empty_string(self, backman, mock_client):
        """テスト6: turnsが空の場合に空文字列を返すこと（LLM呼ばない）"""
        result = backman.generate_summary([])

        assert result == ""
        mock_client.messages.create.assert_not_called()


class TestDetectExplicitMemoryReference:
    def test_keyword_match_returns_true(self, backman):
        """テスト7: 「さっきの件ですが」→ True"""
        assert backman.detect_explicit_memory_reference("さっきの件ですが") is True

    def test_no_keyword_returns_false(self, backman):
        """テスト8: 「今日の天気は」→ False"""
        assert backman.detect_explicit_memory_reference("今日の天気は") is False

    def test_all_keywords_detected(self, backman):
        """全キーワードが検出されること"""
        keywords = ["さっきの", "前に話した", "あの件", "覚えてる", "記憶", "以前", "前回", "さっき言ってた"]
        for kw in keywords:
            assert backman.detect_explicit_memory_reference(f"{kw}について") is True


class TestBuildTaggingPrompt:
    def test_all_few_shot_examples_included(self, backman):
        """テスト9: TAGGING_FEW_SHOT_EXAMPLESの全3件が含まれること"""
        prompt = backman._build_tagging_prompt("テスト入力")

        assert len(TAGGING_FEW_SHOT_EXAMPLES) == 3
        for ex in TAGGING_FEW_SHOT_EXAMPLES:
            assert ex["input"] in prompt

    def test_prompt_contains_input_text(self, backman):
        """プロンプトに入力テキストが含まれること"""
        text = "固有のテスト入力テキスト12345"
        prompt = backman._build_tagging_prompt(text)
        assert text in prompt
