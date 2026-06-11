import re

from app.config import env_int
from app.services.meeting_analysis import _chat, _parse_json, llm_enabled


SYSTEM_PROMPT = """你是一名专业中文演讲稿撰稿人。用户会用自然语言描述需要的演讲稿。
请自动理解主题、场景、听众、语气和时长；用户未说明时，按通用会议发言、自然专业语气、约5分钟处理。
生成一篇完整、连贯、可以直接打印并照着朗读的中文演讲稿，而不是提纲。
必须紧扣用户明确提出的主题、对象、目的和要点，逐项体现在正文中；不得用泛泛的致辞内容替代用户主题。
标题必须直接反映用户主题。若用户要求了时长，正文篇幅按中文正常朗读速度约每分钟240字控制。
不得虚构用户未提供的内部事实、近期新闻、政策、人物言论或精确数据。
涉及时间敏感信息时，应使用稳健概括表达；优先使用用户提供的材料。
正文应包含自然得体的开场、清晰段落和完整结尾。
输出前请自行检查用户的每项明确要求是否都已落实，但不要输出检查过程。
输出必须是 JSON 对象，不要输出 Markdown、思考过程或额外说明：
{"title": "演讲稿标题", "content": "完整演讲稿正文"}"""

REVISION_PROMPT = """你是一名专业中文演讲稿编辑。你会收到当前演讲稿和用户的修改指令。
必须返回修改完成后的整篇演讲稿正文，而不是建议、分析、多个备选方案或局部片段。
用户要求修改开头或结尾时，只修改对应部分，其余正文保持不变。
用户在指令中粘贴了原稿中的一段文字并要求修改时，只替换对应段落，其余正文保持不变。
用户明确要求重写时，围绕原稿主题和原始需求重写全文。
用户未要求修改标题时，标题保持不变。不得虚构用户未提供的事实或数据。
输出必须是 JSON 对象，不要输出 Markdown、思考过程或额外说明：
{"title": "修改后的标题", "content": "修改后的完整演讲稿正文", "message": "一句简短的修改说明"}"""


def count_speech_characters(content: str) -> int:
    return len(re.sub(r"\s+", "", content))


def estimate_minutes(content: str) -> int:
    count = count_speech_characters(content)
    return max(1, round(count / 240)) if count else 0


def describe_revision(original: str, revised: str) -> dict:
    prefix_length = 0
    prefix_limit = min(len(original), len(revised))
    while prefix_length < prefix_limit and original[prefix_length] == revised[prefix_length]:
        prefix_length += 1

    suffix_length = 0
    suffix_limit = min(len(original) - prefix_length, len(revised) - prefix_length)
    while (
        suffix_length < suffix_limit
        and original[len(original) - suffix_length - 1]
        == revised[len(revised) - suffix_length - 1]
    ):
        suffix_length += 1

    original_end = len(original) - suffix_length
    revised_end = len(revised) - suffix_length
    return {
        "start": prefix_length,
        "end": original_end,
        "replacement": revised[prefix_length:revised_end],
    }


def generate_speech(prompt: str) -> dict:
    if not llm_enabled():
        raise RuntimeError("LLM 服务未启用")
    clean_prompt = prompt.strip()
    if not clean_prompt:
        raise ValueError("请输入演讲稿需求描述")
    content = _chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": clean_prompt},
        ],
        temperature=0.45,
        max_tokens=env_int("HUIYI_SPEECH_MAX_TOKENS", 4000),
    )
    data = _parse_json(content)
    title = str(data.get("title") or "未命名演讲稿").strip()
    body = str(data.get("content") or "").strip()
    if not body:
        raise ValueError("LLM 未返回有效演讲稿正文")
    return {
        "title": title[:100],
        "content": body,
        "word_count": count_speech_characters(body),
        "estimated_minutes": estimate_minutes(body),
    }


def revise_speech(speech: dict, instruction: str) -> dict:
    if not llm_enabled():
        raise RuntimeError("LLM 服务未启用")
    clean_instruction = instruction.strip()
    if not clean_instruction:
        raise ValueError("请输入修改要求")
    content = _chat(
        [
            {"role": "system", "content": REVISION_PROMPT},
            {
                "role": "user",
                "content": (
                    f"原始需求：{speech.get('prompt') or '无'}\n"
                    f"当前标题：{speech.get('title') or '未命名演讲稿'}\n"
                    f"当前正文：\n{speech.get('content') or ''}\n\n"
                    f"修改指令：{clean_instruction}"
                ),
            },
        ],
        temperature=0.25,
        max_tokens=env_int("HUIYI_SPEECH_MAX_TOKENS", 4000),
    )
    data = _parse_json(content)
    title = str(data.get("title") or speech.get("title") or "未命名演讲稿").strip()
    body = str(data.get("content") or "").strip()
    message = str(data.get("message") or "已按你的要求修改演讲稿。").strip()
    if not body:
        raise ValueError("LLM 未返回修改后的演讲稿正文")
    return {
        "title": title[:100],
        "content": body,
        "message": message,
        "revision": describe_revision(str(speech.get("content") or ""), body),
        "word_count": count_speech_characters(body),
        "estimated_minutes": estimate_minutes(body),
    }
