"""
tests/test_llm.py — 测试 llm.py 的 temperature/seed 配置传递
"""
from __future__ import annotations


class TestBuildChatLlmDeterminism:
    def test_vision_llm_uses_temperature_zero_and_configured_seed(self, mocker):
        """build_vision_llm 应固定 temperature=0，并传入 settings.llm_seed。"""
        from pdf2md.config import settings
        from pdf2md.llm import build_vision_llm

        llm = build_vision_llm(timeout=settings.page_timeout)

        assert llm.temperature == 0
        assert llm.seed == settings.llm_seed

    def test_orchestrator_llm_uses_temperature_zero_and_configured_seed(self):
        """build_orchestrator_llm 应固定 temperature=0，并传入 settings.llm_seed。"""
        from pdf2md.config import settings
        from pdf2md.llm import build_orchestrator_llm

        llm = build_orchestrator_llm(streaming=False)

        assert llm.temperature == 0
        assert llm.seed == settings.llm_seed

    def test_seed_none_disables_seed_param(self, mocker):
        """settings.llm_seed 为 None 时，不应向 ChatOpenAI 传入 seed 参数。"""
        from pdf2md.config import settings
        from pdf2md.llm import build_vision_llm

        mocker.patch.object(settings, "llm_seed", None)
        llm = build_vision_llm(timeout=settings.page_timeout)

        assert llm.seed is None
