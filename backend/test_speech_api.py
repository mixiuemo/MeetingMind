import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from zipfile import ZipFile

from app import main as api


class FakeRepository:
    def __init__(self) -> None:
        self.speeches = {}

    def create_speech(self, speech_id, prompt, generated):
        now = datetime.now(timezone.utc).isoformat()
        speech = {
            "id": speech_id,
            "prompt": prompt,
            **generated,
            "created_at": now,
            "updated_at": now,
        }
        self.speeches[speech_id] = speech
        return deepcopy(speech)

    def list_speeches(self):
        return [deepcopy(speech) for speech in self.speeches.values()]

    def get_speech(self, speech_id):
        speech = self.speeches.get(speech_id)
        return deepcopy(speech) if speech else None

    def update_speech(self, speech_id, title, content, stats):
        if speech_id not in self.speeches:
            return None
        self.speeches[speech_id].update(title=title, content=content, **stats)
        return deepcopy(self.speeches[speech_id])

    def regenerate_speech(self, speech_id, generated):
        if speech_id not in self.speeches:
            return None
        self.speeches[speech_id].update(generated)
        return deepcopy(self.speeches[speech_id])

    def delete_speech(self, speech_id):
        return self.speeches.pop(speech_id, None) is not None


async def response_bytes(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def main() -> None:
    original_repository = api.repository
    original_generator = api.generate_speech
    api.repository = FakeRepository()
    api.generate_speech = lambda prompt: {
        "title": "AI 效率演讲",
        "content": f"各位同事，大家好。\n\n今天分享：{prompt}",
        "word_count": 24,
        "estimated_minutes": 1,
    }
    try:
        created = await api.create_speech(api.SpeechGenerateRequest(prompt="AI 帮助工作"))
        assert created["title"] == "AI 效率演讲"
        assert len(await api.list_speeches()) == 1

        updated = await api.update_speech(
            created["id"],
            api.SpeechUpdate(title="修改后的标题", content="修改后的完整正文。"),
        )
        assert updated["title"] == "修改后的标题"
        assert updated["word_count"] > 0

        regenerated = await api.regenerate_speech(created["id"])
        assert regenerated["title"] == "AI 效率演讲"

        exported = await api.export_speech_docx(created["id"])
        document = await response_bytes(exported)
        with ZipFile(__import__("io").BytesIO(document)) as archive:
            assert archive.testzip() is None
            assert "AI 效率演讲" in archive.read("word/document.xml").decode("utf-8")

        assert await api.delete_speech(created["id"]) == {"status": "ok"}
        assert await api.list_speeches() == []
        print("speech API tests passed")
    finally:
        api.repository = original_repository
        api.generate_speech = original_generator


if __name__ == "__main__":
    asyncio.run(main())
