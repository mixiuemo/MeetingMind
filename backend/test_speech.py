import asyncio
from io import BytesIO
from zipfile import ZipFile

from app.services import meeting_analysis
from app.exports.speech_word import build_speech_docx
from app.services.speech_writer import count_speech_characters, estimate_minutes


def test_word_export() -> None:
    speech = {
        "title": "人工智能与日常工作",
        "content": "各位同事，大家好。\n\n今天我想和大家聊聊人工智能如何帮助日常工作。",
    }
    document = build_speech_docx(speech)
    assert isinstance(document, BytesIO)
    with ZipFile(document) as archive:
        assert archive.testzip() is None
        xml = archive.read("word/document.xml").decode("utf-8")
        assert speech["title"] in xml
        assert "人工智能如何帮助日常工作" in xml


def test_statistics() -> None:
    content = "人工智能可以帮助我们提高效率。" * 20
    assert count_speech_characters(content) == len(content)
    assert estimate_minutes(content) >= 1


def test_parse_json_repairs_invalid_output() -> None:
    broken = """```json
{
  "title": "AI 演讲稿",
  "content": "第一段提到 "AI 助手" 和会议总结。"
}
```"""
    original_chat = meeting_analysis._chat
    meeting_analysis._chat = lambda *args, **kwargs: (
        '{"title":"AI 演讲稿","content":"第一段提到 \\"AI 助手\\" 和会议总结。"}'
    )
    try:
        parsed = meeting_analysis._parse_json(broken)
    finally:
        meeting_analysis._chat = original_chat
    assert parsed["title"] == "AI 演讲稿"
    assert "AI 助手" in parsed["content"]


async def main() -> None:
    test_statistics()
    test_word_export()
    test_parse_json_repairs_invalid_output()
    print("speech tests passed")


if __name__ == "__main__":
    asyncio.run(main())
