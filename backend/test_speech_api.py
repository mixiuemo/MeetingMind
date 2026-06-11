import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from zipfile import ZipFile

from app import main as api
from app.services.speech_writer import describe_revision


class FakeRepository:
    def __init__(self) -> None:
        self.speeches = {}
        self.sessions = {}

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

    def get_chat_session(self, session_id):
        session = self.sessions.get(session_id)
        return deepcopy(session) if session else None

    def update_chat_session(self, session_id, *, summary, messages):
        if session_id not in self.sessions:
            return None
        self.sessions[session_id]["summary"] = deepcopy(summary)
        self.sessions[session_id]["messages"] = deepcopy(messages)
        return deepcopy(self.sessions[session_id])


async def response_bytes(response) -> bytes:
    return b"".join([chunk async for chunk in response.body_iterator])


async def main() -> None:
    assert describe_revision("开头\n旧结尾", "开头\n新结尾") == {
        "start": 3,
        "end": 4,
        "replacement": "新",
    }
    original_repository = api.repository
    original_generator = api.generate_speech
    original_reviser = api.revise_speech
    api.repository = FakeRepository()
    api.generate_speech = lambda prompt: {
        "title": "AI 效率演讲",
        "content": f"各位同事，大家好。\n\n今天分享：{prompt}",
        "word_count": 24,
        "estimated_minutes": 1,
    }
    api.revise_speech = lambda speech, instruction: {
        "title": speech["title"],
        "content": f"{speech['content']}\n\n修改后的结尾。",
        "message": f"已完成：{instruction}",
        "revision": {
            "start": len(speech["content"]),
            "end": len(speech["content"]),
            "replacement": "\n\n修改后的结尾。",
        },
        "word_count": 32,
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

        api.repository.sessions["speech-session"] = {
            "id": "speech-session",
            "mode": "speech",
            "target_id": created["id"],
            "summary": None,
            "messages": [],
        }
        revised = await api.revise_selected_speech(
            created["id"],
            api.SpeechRevisionRequest(instruction="把结尾改掉", session_id="speech-session"),
        )
        assert revised["message"] == "已完成：把结尾改掉"
        assert revised["speech"]["content"].endswith("修改后的结尾。")
        assert revised["revision"]["replacement"].endswith("修改后的结尾。")
        assert [message["role"] for message in revised["session"]["messages"]] == ["user", "assistant"]
        assert api.repository.get_chat_session("speech-session")["messages"][-1]["content"] == "已完成：把结尾改掉"

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
        api.revise_speech = original_reviser


if __name__ == "__main__":
    asyncio.run(main())
