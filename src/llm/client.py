"""LLM 客户端与 JSON 解析工具"""
import json
import re

from openai import AsyncOpenAI

from src.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return _client


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def parse_json_loose(text: str):
    """容错解析：去掉 markdown 代码块、截取第一个 JSON 数组/对象"""
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试截取数组
    for left, right in (("[", "]"), ("{", "}")):
        start = text.find(left)
        end = text.rfind(right)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法解析 LLM 返回的 JSON: {text[:200]}")


async def chat(system: str, user: str, temperature: float = 0.1) -> str:
    client = get_client()
    resp = await client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""
