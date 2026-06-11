import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import time

from app import main as api


class FakeRepository:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.meetings = {
            "meeting-1": {
                "id": "meeting-1",
                "title": "周会",
                "segments": [{"id": "s1", "speaker": "发言人", "text": "讨论项目排期"}],
                "analysis": {"summary": "讨论了项目排期", "action_items": []},
            }
        }
        self.speeches = {
            "speech-1": {
                "id": "speech-1",
                "title": "AI 演讲稿",
                "prompt": "写一篇 AI 演讲稿",
                "content": "大家好，今天聊聊 AI。",
                "word_count": 10,
                "estimated_minutes": 1,
                "created_at": now,
                "updated_at": now,
            }
        }
        self.sessions = {}

    def get_meeting(self, meeting_id):
        return deepcopy(self.meetings.get(meeting_id))

    def get_speech(self, speech_id):
        return deepcopy(self.speeches.get(speech_id))

    def create_chat_session(self, session_id, mode, target_id, title):
        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": session_id,
            "mode": mode,
            "target_id": target_id,
            "title": title,
            "summary": None,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        self.sessions[session_id] = session
        return deepcopy(session)

    def list_chat_sessions(self, mode=None, target_id=None):
        items = list(self.sessions.values())
        if mode:
            items = [item for item in items if item["mode"] == mode]
        if target_id is not None:
            items = [item for item in items if item["target_id"] == target_id]
        return [deepcopy(item) for item in items]

    def get_chat_session(self, session_id):
        session = self.sessions.get(session_id)
        return deepcopy(session) if session else None

    def update_chat_session(self, session_id, *, summary, messages):
        if session_id not in self.sessions:
            return None
        self.sessions[session_id]["summary"] = deepcopy(summary)
        self.sessions[session_id]["messages"] = deepcopy(messages)
        self.sessions[session_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        return deepcopy(self.sessions[session_id])

    def delete_chat_session(self, session_id):
        return self.sessions.pop(session_id, None) is not None


async def main() -> None:
    original_repository = api.repository
    original_generator = api.generate_chat_reply
    original_stream_generator = api.generate_chat_reply_stream
    api.repository = FakeRepository()
    api.generate_chat_reply = lambda mode, user_message, **kwargs: {
        "summary": {
            "user_goal": "聊天",
            "current_task": "回答问题",
            "decisions": [],
            "important_facts": [],
            "open_questions": [],
        },
        "messages": [
            {"id": "u1", "role": "user", "content": user_message},
            {"id": "a1", "role": "assistant", "content": f"{mode}:{user_message}"},
        ],
        "assistant_message": {"id": "a1", "role": "assistant", "content": f"{mode}:{user_message}"},
        "user_message": {"id": "u1", "role": "user", "content": user_message},
    }

    def fake_stream_generator(mode, user_message, **kwargs):
        yield {
            "type": "start",
            "user_message_id": "stream-u1",
            "assistant_message_id": "stream-a1",
        }
        time.sleep(0.08)
        yield {"type": "content", "content": "流"}
        time.sleep(0.08)
        messages = [
            {"id": "stream-u1", "role": "user", "content": user_message},
            {"id": "stream-a1", "role": "assistant", "content": "流式回复"},
        ]
        yield {"type": "content", "content": "式回复"}
        yield {
            "type": "done",
            "summary": {
                "user_goal": "聊天",
                "current_task": "测试流式输出",
                "decisions": [],
                "important_facts": [],
                "open_questions": [],
            },
            "messages": messages,
            "user_message": messages[0],
            "assistant_message": messages[1],
        }

    api.generate_chat_reply_stream = fake_stream_generator
    try:
        session = await api.create_chat_session(api.ChatSessionCreateRequest(mode="meeting", target_id="meeting-1"))
        assert session["mode"] == "meeting"
        assert "周会" in session["title"]

        listed = await api.list_chat_sessions(mode="meeting", target_id="meeting-1")
        assert len(listed) == 1

        result = await api.send_chat_message(session["id"], api.ChatMessageRequest(content="帮我总结一下"))
        assert result["session"]["messages"][-1]["content"] == "meeting:帮我总结一下"

        fetched = await api.get_chat_session(session["id"])
        assert fetched["summary"]["current_task"] == "回答问题"

        started = time.perf_counter()
        stream_response = await api.send_chat_message_stream(
            session["id"],
            api.ChatMessageRequest(content="测试流式输出"),
        )
        stream_iterator = stream_response.body_iterator
        first_chunk = await anext(stream_iterator)
        assert '"type": "start"' in first_chunk
        assert time.perf_counter() - started < 0.06
        remaining_chunks = [chunk async for chunk in stream_iterator]
        assert any('"content": "流"' in chunk for chunk in remaining_chunks)
        assert any('"type": "done"' in chunk for chunk in remaining_chunks)

        deleted = await api.delete_chat_session(session["id"])
        assert deleted == {"status": "ok"}
        print("chat API tests passed")
    finally:
        api.repository = original_repository
        api.generate_chat_reply = original_generator
        api.generate_chat_reply_stream = original_stream_generator


if __name__ == "__main__":
    asyncio.run(main())
