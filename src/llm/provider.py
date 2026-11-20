"""LLM 可用性判定与模型端点解析

关键概念：

- WebSkill 是一个**独立进程**，它不会自动继承上层 Agent 的模型访问权限。
  上层 Agent 能调模型，是因为 Agent 自己持有凭据 / 网关；WebSkill 要调模型，
  必须有它自己的一条通道。API key 只是这条通道的一种凭据形式，不是唯一形式。

- 所以「谁来规划」有两条路：
  1) 上层 Agent 规划好，把 steps 传给 WebSkill（**完全不需要 key**）
  2) WebSkill 自己规划，此时它需要一个模型端点：云端 key / 本地模型 / Agent 的模型网关

- 只要把 OPENAI_BASE_URL 指向一个自定义端点（Ollama、vLLM、Agent 自建的 OpenAI 兼容网关），
  就不需要真实 API key —— 本地/内网端点通常不做鉴权。

模式（环境变量 LLM_PROVIDER）：
- auto（默认）：按端点与 key 判定是否可用
- none / external：内部完全不调用 LLM
"""
import os

from src.config import OPENAI_API_KEY, OPENAI_BASE_URL

_PLACEHOLDER_KEYS = {"", "sk-xxx", "sk-你的key", "your-key", "none", "null"}

# 官方端点需要真实 key；其他端点视为用户自建的网关/本地模型
_OFFICIAL_HOSTS = ("api.openai.com",)


def provider_name() -> str:
    return os.getenv("LLM_PROVIDER", "auto").strip().lower()


def endpoint_is_custom() -> bool:
    """是否指向非官方端点（本地模型 / Agent 网关 / 其他兼容服务）"""
    url = (OPENAI_BASE_URL or "").strip().lower()
    if not url:
        return False
    return not any(host in url for host in _OFFICIAL_HOSTS)


def llm_available() -> bool:
    """内部是否可以调用 LLM"""
    mode = provider_name()
    if mode in ("none", "external"):
        return False

    key = (OPENAI_API_KEY or "").strip()
    if key.lower() not in _PLACEHOLDER_KEYS:
        return True

    # 没有 key 但端点已指向自定义网关 / 本地模型 —— 视为可用，不需要 key
    return endpoint_is_custom()


def describe() -> dict:
    return {
        "provider": provider_name(),
        "llm_available": llm_available(),
        "base_url": OPENAI_BASE_URL,
        "endpoint_is_custom": endpoint_is_custom(),
        # 仅表示「WebSkill 自己规划」是否需要 key；上层 Agent 传 steps 永远不需要
        "key_required": not endpoint_is_custom(),
        "fallback_planner": "local_rules",
        "planners": ["external_steps", "local_rules", "llm"],
    }
