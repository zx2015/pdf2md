"""
tests/test_model_registry.py — 测试模型 context window / 最大输出 token 数的分层探测逻辑
"""
from __future__ import annotations


class TestGetModelLimits:
    def test_exact_match_known_model(self):
        """精确登记的模型应直接返回静态表中的数值。"""
        from pdf2md.model_registry import _KNOWN_MODELS, get_model_limits

        model = "Qwen/Qwen3.5-4B"
        result = get_model_limits(model)

        assert result == _KNOWN_MODELS[model]
        assert result.context_window > 0
        assert result.max_output_tokens > 0

    def test_prefix_fallback_for_unregistered_variant(self):
        """未逐一登记但匹配已知前缀的型号应按前缀模糊匹配。"""
        from pdf2md.model_registry import get_model_limits

        result = get_model_limits("Qwen/Qwen3-VL-4B-Custom-Variant")

        assert result.max_output_tokens == 8192
        assert result.context_window == 262144

    def test_unknown_model_falls_back_to_safe_default(self):
        """完全无法识别的模型应返回保守默认值，不应抛出异常。"""
        from pdf2md.model_registry import _SAFE_DEFAULT, get_model_limits

        result = get_model_limits("some-vendor/totally-unknown-model-v99")

        assert result == _SAFE_DEFAULT

    def test_litellm_not_installed_skips_that_layer(self, mocker):
        """litellm 未安装时应静默跳过该层，不影响后续兜底逻辑。"""
        from pdf2md.model_registry import _lookup_litellm

        # 环境中未安装 litellm，_lookup_litellm 应返回 None 而不是抛异常
        result = _lookup_litellm("Qwen/Qwen3.5-4B")
        assert result is None

    def test_litellm_layer_used_when_available(self, mocker):
        """litellm 可用且返回有效数据时，应优先于前缀模糊匹配使用其结果。"""
        import sys
        import types

        fake_litellm = types.ModuleType("litellm")
        fake_litellm.get_model_info = lambda model: {
            "max_input_tokens": 999999,
            "max_output_tokens": 12345,
        }
        mocker.patch.dict(sys.modules, {"litellm": fake_litellm})

        from pdf2md.model_registry import get_model_limits

        # 用一个不在精确表、也不匹配任何前缀的模型名，确保命中的是 litellm 层
        result = get_model_limits("some-brand-new/unregistered-model")

        assert result.context_window == 999999
        assert result.max_output_tokens == 12345


class TestEffectiveVisionMaxTokens:
    def test_explicit_override_takes_priority(self):
        """.env 显式设置 vision_max_tokens 时，应直接使用该值，不触发自动探测。"""
        from pdf2md.config import Settings

        s = Settings(vision_max_tokens=1234, siliconflow_api_key="x", openai_api_key="x")
        assert s.effective_vision_max_tokens == 1234

    def test_auto_detect_when_not_set(self):
        """未显式设置时应按 vision_chat_model 自动探测。"""
        from pdf2md.config import Settings
        from pdf2md.model_registry import get_model_limits

        s = Settings(
            vision_max_tokens=None,
            vision_chat_model="Qwen/Qwen3.5-4B",
            siliconflow_api_key="x",
            openai_api_key="x",
        )
        expected = get_model_limits("Qwen/Qwen3.5-4B").max_output_tokens
        assert s.effective_vision_max_tokens == expected
