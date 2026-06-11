import json
import os
from urllib.request import Request, urlopen
from uuid import uuid4

from app.services.meeting_analysis import _chat, _parse_json, llm_enabled
from app.config import env_int


MAX_RAW_MESSAGES = 10
KEEP_RECENT_MESSAGES = 4
MAX_CONTEXT_CHARACTERS = 7000


SUMMARY_SCHEMA = {
    "user_goal": "",
    "current_task": "",
    "decisions": [],
    "important_facts": [],
    "open_questions": [],
}


SYSTEM_PROMPTS = {
    "free": (
        "你是会议智能记录系统里的日常 AI 助手。"
        "回答自然、简洁、友好，优先帮助用户梳理思路、改写表达、解释问题。"
    ),
    "meeting": (
        "你是会议助手。优先基于当前会议原文、纪要和待办回答。"
        "可以总结、提炼、改写、生成汇报口径，但不要编造会议中没有出现的结论。"
    ),
    "speech": (
        "你是演讲稿助手。优先基于当前演讲稿标题、正文和原始需求回答。"
        "可以缩写、扩写、改语气、重写开头和结尾，并保持文本可直接朗读。"
    ),
}


SUMMARY_PROMPT = """你是对话摘要助手。请把已有对话整理成结构化 JSON。
不要编造没有明确提到的信息，字段缺失时返回空字符串或空数组。
输出必须是 JSON 对象：
{
  "user_goal": "用户主要目标",
  "current_task": "当前正在处理的任务",
  "decisions": ["已经明确的决定"],
  "important_facts": ["必须保留的重要事实"],
  "open_questions": ["仍待确认的问题"]
}"""


def _normalize_summary(summary: dict | None) -> dict:
    base = dict(SUMMARY_SCHEMA)
    if not isinstance(summary, dict):
        return base
    base["user_goal"] = str(summary.get("user_goal") or "").strip()
    base["current_task"] = str(summary.get("current_task") or "").strip()
    for key in ("decisions", "important_facts", "open_questions"):
        items = summary.get(key, [])
        base[key] = [str(item).strip() for item in items if str(item).strip()]
    return base


def _summary_text(summary: dict) -> str:
    summary = _normalize_summary(summary)
    return json.dumps(summary, ensure_ascii=False)


def _message_characters(messages: list[dict]) -> int:
    return sum(len(str(message.get("content") or "")) for message in messages)


def _needs_compaction(messages: list[dict]) -> bool:
    return len(messages) > MAX_RAW_MESSAGES or _message_characters(messages) > MAX_CONTEXT_CHARACTERS


def _stringify_meeting_context(meeting: dict | None) -> str:
    if not meeting:
        return "当前没有选中的会议。"
    analysis = meeting.get("analysis") or {}
    summary = str(analysis.get("summary") or "").strip()
    action_items = analysis.get("action_items") or []
    action_text = "\n".join(
        f"- {item.get('task') or ''}；负责人：{item.get('owner') or '未指定'}；截止：{item.get('deadline') or '未指定'}"
        for item in action_items[:8]
        if item.get("task")
    ) or "无明确待办"
    transcript = "\n".join(
        f"[{index + 1}] {segment.get('speaker') or '发言人'}：{segment.get('text') or ''}"
        for index, segment in enumerate((meeting.get("segments") or [])[-12:])
    ) or "暂无转写原文"
    return (
        f"会议标题：{meeting.get('title') or '未命名会议'}\n"
        f"会议摘要：{summary or '尚未生成 AI 纪要'}\n"
        f"待办事项：\n{action_text}\n"
        f"最近原文：\n{transcript}"
    )


def _stringify_speech_context(speech: dict | None) -> str:
    if not speech:
        return "当前没有选中的演讲稿。"
    return (
        f"演讲稿标题：{speech.get('title') or '未命名演讲稿'}\n"
        f"原始需求：{speech.get('prompt') or '无'}\n"
        f"当前字数：{speech.get('word_count') or 0}，预计时长：{speech.get('estimated_minutes') or 0} 分钟\n"
        f"正文：\n{speech.get('content') or ''}"
    )


def build_chat_context(mode: str, *, meeting: dict | None = None, speech: dict | None = None) -> str:
    if mode == "meeting":
        return _stringify_meeting_context(meeting)
    if mode == "speech":
        return _stringify_speech_context(speech)
    return "当前为自由聊天模式，没有绑定会议或演讲稿。"


def _summarize_messages(mode: str, summary: dict, messages: list[dict]) -> dict:
    existing_summary = _summary_text(summary)
    content = _chat(
        [
            {"role": "system", "content": SUMMARY_PROMPT},
            {
                "role": "user",
                "content": (
                    f"模式：{mode}\n"
                    f"已有摘要：{existing_summary}\n"
                    f"对话消息：{json.dumps(messages, ensure_ascii=False)}"
                ),
            },
        ],
    )
    return _normalize_summary(_parse_json(content))


def compact_session_history(mode: str, summary: dict, messages: list[dict]) -> tuple[dict, list[dict]]:
    if not _needs_compaction(messages):
        return _normalize_summary(summary), messages
    old_messages = messages[:-KEEP_RECENT_MESSAGES]
    recent_messages = messages[-KEEP_RECENT_MESSAGES:]
    next_summary = _summarize_messages(mode, summary, old_messages)
    return next_summary, recent_messages


def _chat_messages_for_llm(summary: dict, context_text: str, messages: list[dict], user_message: str) -> list[dict]:
    payload = [
        {"role": "system", "content": context_text},
    ]
    normalized_summary = _normalize_summary(summary)
    if any(normalized_summary.values()):
        payload.append({"role": "system", "content": f"历史摘要：{_summary_text(normalized_summary)}"})
    payload.extend(
        {
            "role": message["role"],
            "content": message["content"],
        }
        for message in messages
    )
    payload.append({"role": "user", "content": user_message})
    return payload


def generate_chat_reply(
    mode: str,
    user_message: str,
    *,
    summary: dict | None = None,
    messages: list[dict] | None = None,
    meeting: dict | None = None,
    speech: dict | None = None,
) -> dict:
    if not llm_enabled():
        raise RuntimeError("LLM 服务未启用")
    clean_message = user_message.strip()
    if not clean_message:
        raise ValueError("请输入消息内容")
    current_messages = list(messages or [])
    compacted_summary, compacted_messages = compact_session_history(
        mode,
        summary or SUMMARY_SCHEMA,
        current_messages,
    )
    context_text = (
        f"{SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS['free'])}\n"
        f"当前业务上下文：\n{build_chat_context(mode, meeting=meeting, speech=speech)}"
    )
    reply = _chat(
        _chat_messages_for_llm(compacted_summary, context_text, compacted_messages, clean_message),
        temperature=0.35,
        response_format=None,
        max_tokens=1800,
    ).strip()
    if not reply:
        raise ValueError("LLM 未返回有效回复")
    user_record = {
        "id": str(uuid4()),
        "role": "user",
        "content": clean_message,
    }
    assistant_record = {
        "id": str(uuid4()),
        "role": "assistant",
        "content": reply,
    }
    next_messages = [*compacted_messages, user_record, assistant_record]
    next_summary, next_messages = compact_session_history(mode, compacted_summary, next_messages)
    return {
        "summary": next_summary,
        "messages": next_messages,
        "assistant_message": assistant_record,
        "user_message": user_record,
    }


def generate_chat_reply_stream(
    mode: str,
    user_message: str,
    *,
    summary: dict | None = None,
    messages: list[dict] | None = None,
    meeting: dict | None = None,
    speech: dict | None = None,
):
    """生成流式聊天回复"""
    if not llm_enabled():
        raise RuntimeError("LLM 服务未启用")
    clean_message = user_message.strip()
    if not clean_message:
        raise ValueError("请输入消息内容")
    current_messages = list(messages or [])
    compacted_summary, compacted_messages = compact_session_history(
        mode,
        summary or SUMMARY_SCHEMA,
        current_messages,
    )
    context_text = (
        f"{SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS['free'])}\n"
        f"当前业务上下文：\n{build_chat_context(mode, meeting=meeting, speech=speech)}"
    )

    # 生成用户消息ID
    user_id = str(uuid4())
    assistant_id = str(uuid4())

    # 发送初始事件
    yield {
        "type": "start",
        "user_message_id": user_id,
        "assistant_message_id": assistant_id,
    }

    # 调用LLM流式API
    try:
        base_url = os.getenv("HUIYI_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        body = {
            "model": os.getenv("HUIYI_LLM_MODEL", "qwen3.5:4b"),
            "messages": _chat_messages_for_llm(compacted_summary, context_text, compacted_messages, clean_message),
            "temperature": 0.35,
            "stream": True,
            "enable_thinking": False,
            "keep_alive": -1,
            "max_tokens": 1800,
        }
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

        full_content = ""
        with urlopen(request, timeout=env_int("HUIYI_LLM_TIMEOUT_SECONDS", 180)) as response:
            for raw_line in response:
                line = raw_line.strip()
                if not line or line == b"data: [DONE]":
                    continue
                if not line.startswith(b"data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip().decode("utf-8"))
                    content = data["choices"][0].get("delta", {}).get("content")
                    if content:
                        full_content += content
                        yield {
                            "type": "content",
                            "content": content,
                        }
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError, IndexError):
                    continue

        if not full_content.strip():
            raise ValueError("LLM 流式接口未返回正文内容")

        # 构建完整消息记录
        user_record = {
            "id": user_id,
            "role": "user",
            "content": clean_message,
        }
        assistant_record = {
            "id": assistant_id,
            "role": "assistant",
            "content": full_content.strip(),
        }
        next_messages = [*compacted_messages, user_record, assistant_record]
        next_summary, next_messages = compact_session_history(mode, compacted_summary, next_messages)

        # 发送完成事件
        yield {
            "type": "done",
            "summary": next_summary,
            "messages": next_messages,
            "user_message": user_record,
            "assistant_message": assistant_record,
        }
    except Exception as error:
        yield {
            "type": "error",
            "error": str(error),
        }
