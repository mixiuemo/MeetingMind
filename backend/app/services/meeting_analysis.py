import json
import os
import re
from urllib.request import Request, urlopen

from app.config import env_bool, env_int


JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_PROMPT = """你是严谨的中文会议纪要助手。请仅依据提供的会议转写生成分析，不得编造。
仅提取会议中明确提出、承诺或分配的待办；没有明确待办时 action_items 必须为空数组。
负责人或截止日期未明确时必须返回 null。
输出必须是一个 JSON 对象，不要输出 Markdown、思考过程或额外说明。
JSON 结构：
{
  "summary": "简洁会议摘要",
  "key_points": [{"text": "核心要点", "source_segment_ids": ["原文段落ID"]}],
  "decisions": [{"text": "明确决定或结论", "source_segment_ids": ["原文段落ID"]}],
  "open_questions": [{"text": "尚未解决的问题", "source_segment_ids": ["原文段落ID"]}],
  "action_items": [{
    "task": "明确待办",
    "owner": null,
    "deadline": null,
    "source_segment_ids": ["原文段落ID"]
  }]
}"""


def llm_enabled() -> bool:
    return env_bool("HUIYI_LLM_ENABLED", True)


def _chat(messages: list[dict]) -> str:
    base_url = os.getenv("HUIYI_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    body = {
        "model": os.getenv("HUIYI_LLM_MODEL", "qwen3.5:4b"),
        "messages": messages,
        "temperature": 0.1,
        "stream": False,
        "keep_alive": -1,
        "think": False,
        "max_tokens": env_int("HUIYI_LLM_MAX_TOKENS", 1600),
        "response_format": {"type": "json_object"},
    }
    reasoning_effort = os.getenv("HUIYI_LLM_REASONING_EFFORT", "").strip()
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {os.getenv('HUIYI_LLM_API_KEY', 'ollama')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(
        request,
        timeout=env_int("HUIYI_LLM_TIMEOUT_SECONDS", 180),
    ) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def _parse_json(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = JSON_BLOCK.search(content)
        if not match:
            raise ValueError("LLM 未返回有效 JSON")
        return json.loads(match.group(0))


def _normalize_items(items, text_key: str, valid_ids: set[str]) -> list[dict]:
    result = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            item = {text_key: item}
        if not isinstance(item, dict) or not str(item.get(text_key) or "").strip():
            continue
        normalized = {text_key: str(item[text_key]).strip()}
        if text_key == "task":
            normalized["owner"] = item.get("owner") or None
            normalized["deadline"] = item.get("deadline") or None
        normalized["source_segment_ids"] = [
            segment_id
            for segment_id in item.get("source_segment_ids", [])
            if segment_id in valid_ids
        ]
        result.append(normalized)
    return result


def _normalize_analysis(data: dict, valid_ids: set[str]) -> dict:
    return {
        "summary": str(data.get("summary") or "").strip(),
        "key_points": _normalize_items(data.get("key_points"), "text", valid_ids),
        "decisions": _normalize_items(data.get("decisions"), "text", valid_ids),
        "open_questions": _normalize_items(
            data.get("open_questions"), "text", valid_ids
        ),
        "action_items": _normalize_items(
            data.get("action_items"), "task", valid_ids
        ),
    }


def _segment_lines(segments: list[dict]) -> list[str]:
    return [
        f"[{segment['id']}] {segment.get('speaker') or '发言人'}：{segment.get('text') or ''}"
        for segment in segments
        if str(segment.get("text") or "").strip()
    ]


def _split_lines(lines: list[str], limit: int) -> list[str]:
    chunks = []
    current = []
    size = 0
    for line in lines:
        if current and size + len(line) > limit:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def analyze_meeting(meeting: dict) -> dict:
    if not llm_enabled():
        raise RuntimeError("LLM 分析未启用")
    segments = meeting.get("segments", [])
    valid_ids = {segment["id"] for segment in segments}
    chunks = _split_lines(
        _segment_lines(segments),
        env_int("HUIYI_LLM_CHUNK_CHARACTERS", 8000),
    )
    if not chunks:
        return _normalize_analysis({}, valid_ids)

    partials = []
    for index, chunk in enumerate(chunks, start=1):
        content = _chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"会议标题：{meeting.get('title')}\n"
                    f"这是第 {index}/{len(chunks)} 部分转写：\n{chunk}",
                },
            ]
        )
        partials.append(_normalize_analysis(_parse_json(content), valid_ids))

    if len(partials) == 1:
        return partials[0]

    content = _chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "请合并以下分段会议分析，去重后生成整场会议最终结果。"
                "必须保留有效 source_segment_ids，仍不得编造待办。\n"
                + json.dumps(partials, ensure_ascii=False),
            },
        ]
    )
    return _normalize_analysis(_parse_json(content), valid_ids)
