from datetime import datetime, timezone

from app.storage.mongo import MeetingRepository, utc_isoformat


def main() -> None:
    timestamp = "2026-06-11T08:00:00+00:00"
    assert utc_isoformat(timestamp) == timestamp
    assert utc_isoformat(datetime(2026, 6, 11, 8, tzinfo=timezone.utc)) == timestamp

    session = MeetingRepository._serialize_chat_session(
        {
            "_id": "session-1",
            "mode": "speech",
            "target_id": "speech-1",
            "title": "演讲稿助手",
            "summary": None,
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "content": "旧字符串时间",
                    "created_at": timestamp,
                },
                {
                    "id": "message-2",
                    "role": "assistant",
                    "content": "缺失时间",
                },
            ],
            "created_at": timestamp,
            "updated_at": datetime(2026, 6, 11, 8, tzinfo=timezone.utc),
        }
    )
    assert session["messages"][0]["created_at"] == timestamp
    assert session["messages"][1]["created_at"] == timestamp
    print("mongo serialization tests passed")


if __name__ == "__main__":
    main()
