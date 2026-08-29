"""
model_registry.py — 探测视觉模型的 context window / 最大输出 token 数。

分层兜底策略（优先级从高到低）：
1. .env 显式配置（VISION_MAX_TOKENS）—— 由调用方（config.Settings）在使用本模块
   结果之前先行处理，本模块不感知；仅在用户未显式配置时才会被调用。
2. 项目内精确匹配的静态表（_KNOWN_MODELS）—— 人工登记、已在本项目中实测验证过
   的模型，可信度最高。
3. 若安装了可选依赖 litellm，复用其社区维护的模型元数据注册表
   （model_prices_and_context_window.json，覆盖数百个主流模型，持续更新）。
   未安装时自动跳过此层，不引入强制依赖。
4. 项目内前缀模糊匹配（_PREFIX_PATTERNS）—— 覆盖同系列但未逐一登记的型号
   （如 Qwen3-VL 系列的其他尺寸变体）。
5. 均未命中时，返回保守的安全默认值（_SAFE_DEFAULT）并记录 WARNING 日志，
   提示用户在 .env 中显式设置 VISION_MAX_TOKENS 覆盖，避免静默使用可能与实际
   不符的数值。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelLimits:
    """模型的容量限制。"""

    context_window: int      # 总上下文窗口（输入+输出）的 token 数上限
    max_output_tokens: int    # 单次响应最大输出 token 数上限


# ── Layer 2：精确匹配的静态表 ──────────────────────────────────────────────
# 人工登记、已在本项目中实测验证过图像输入能力的模型。数值来自各模型官方文档，
# 如 Provider（如 SiliconFlow）对具体模型做了阉割部署，实际值可能更小，此时
# 请在 .env 中用 VISION_MAX_TOKENS 显式覆盖（优先级高于本注册表）。
_KNOWN_MODELS: dict[str, ModelLimits] = {
    "Qwen/Qwen3.5-4B": ModelLimits(context_window=131072, max_output_tokens=8192),
    "Qwen/Qwen3-VL-8B-Instruct": ModelLimits(context_window=262144, max_output_tokens=8192),
    "Qwen/Qwen3-VL-32B-Instruct": ModelLimits(context_window=262144, max_output_tokens=8192),
    "gpt-4o": ModelLimits(context_window=128000, max_output_tokens=16384),
    "gpt-4o-mini": ModelLimits(context_window=128000, max_output_tokens=16384),
}

# ── Layer 4：前缀模糊匹配 ─────────────────────────────────────────────────
# 精确表和 litellm 注册表都未命中时，按模型名前缀归类到同系列的典型值。
# 按列表顺序匹配，越靠前优先级越高，因此更具体的前缀需排在更通用的前缀之前。
_PREFIX_PATTERNS: list[tuple[str, ModelLimits]] = [
    ("Qwen/Qwen3-VL-", ModelLimits(context_window=262144, max_output_tokens=8192)),
    ("Qwen/Qwen3.", ModelLimits(context_window=131072, max_output_tokens=8192)),
    ("Qwen/Qwen2.5-VL", ModelLimits(context_window=131072, max_output_tokens=8192)),
    ("Qwen/Qwen2.5-", ModelLimits(context_window=131072, max_output_tokens=8192)),
    ("gpt-4o", ModelLimits(context_window=128000, max_output_tokens=16384)),
    ("gpt-4", ModelLimits(context_window=128000, max_output_tokens=4096)),
    ("deepseek-", ModelLimits(context_window=65536, max_output_tokens=8192)),
]

# ── Layer 5：安全默认值 ──────────────────────────────────────────────────
# 完全查不到任何匹配时的保守兜底，避免静默使用一个可能过大（导致 API 报错）
# 或过小（浪费模型能力）的数值。
_SAFE_DEFAULT = ModelLimits(context_window=8192, max_output_tokens=2048)


def _lookup_litellm(model: str) -> ModelLimits | None:
    """Layer 3：若安装了可选依赖 litellm，复用其公开维护的模型元数据。

    未安装或查询失败时返回 None，调用方据此继续尝试后续兜底层，不抛出异常。
    """
    try:
        import litellm
    except ImportError:
        return None

    try:
        info = litellm.get_model_info(model)
    except Exception as exc:
        logger.debug("litellm 未找到模型 %s 的元数据: %s", model, exc)
        return None

    context_window = info.get("max_input_tokens") or info.get("max_tokens")
    max_output = info.get("max_output_tokens") or context_window
    if not context_window:
        return None
    return ModelLimits(context_window=int(context_window), max_output_tokens=int(max_output))


def get_model_limits(model: str) -> ModelLimits:
    """按 Layer 2 → 3 → 4 → 5 的顺序探测模型容量限制，返回 ModelLimits。

    调用前提：调用方（如 config.Settings）已确认用户未在 .env 显式覆盖
    （Layer 1），本函数不感知用户配置，只负责"自动探测"这一部分。
    """
    if model in _KNOWN_MODELS:
        return _KNOWN_MODELS[model]

    litellm_result = _lookup_litellm(model)
    if litellm_result is not None:
        logger.info("模型 %s 的容量限制通过 litellm 注册表探测得到: %s", model, litellm_result)
        return litellm_result

    for prefix, limits in _PREFIX_PATTERNS:
        if model.startswith(prefix):
            return limits

    logger.warning(
        "未能识别模型 %s 的 context window/最大输出 token 数，使用保守默认值 %s；"
        "建议在 .env 中显式设置 VISION_MAX_TOKENS 覆盖。",
        model, _SAFE_DEFAULT,
    )
    return _SAFE_DEFAULT
