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


def count_speech_characters(content: str) -> int:
    return len(re.sub(r"\s+", "", content))


def estimate_minutes(content: str) -> int:
    count = count_speech_characters(content)
    return max(1, round(count / 240)) if count else 0


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
