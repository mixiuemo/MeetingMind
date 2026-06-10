import os
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class MeetingRepository:
    def __init__(self) -> None:
        uri = os.getenv("HUIYI_MONGODB_URI", "mongodb://127.0.0.1:27017")
        database_name = os.getenv("HUIYI_MONGODB_DATABASE", "huiyi")
        self.client = MongoClient(uri, serverSelectionTimeoutMS=3_000)
        self.client.admin.command("ping")
        database = self.client[database_name]
        self.meetings = database["meetings"]
        self.segments = database["transcript_segments"]
        self.meetings.create_index([("started_at", DESCENDING)])
        self.segments.create_index(
            [("meeting_id", ASCENDING), ("sequence", ASCENDING)], unique=True
        )

    def create_meeting(self, meeting_id: str, title: str, audio_path: str) -> None:
        now = utc_now()
        self.meetings.insert_one(
            {
                "_id": meeting_id,
                "title": title,
                "status": "recording",
                "started_at": now,
                "ended_at": None,
                "duration_ms": 0,
                "audio_path": audio_path,
                "analysis_status": "not_started",
                "analysis": None,
                "analysis_error": None,
                "created_at": now,
                "updated_at": now,
            }
        )

    def finish_meeting(self, meeting_id: str, duration_ms: int) -> None:
        now = utc_now()
        self.meetings.update_one(
            {"_id": meeting_id},
            {
                "$set": {
                    "status": "ended",
                    "ended_at": now,
                    "duration_ms": duration_ms,
                    "updated_at": now,
                }
            },
        )

    def save_segment(self, segment: dict) -> None:
        now = utc_now()
        document = {
            **segment,
            "_id": segment["id"],
            "created_at": now,
            "updated_at": now,
        }
        document.pop("id", None)
        self.segments.insert_one(document)

    def update_segment(self, meeting_id: str, segment_id: str, edited_text: str) -> bool:
        result = self.segments.update_one(
            {"_id": segment_id, "meeting_id": meeting_id},
            {"$set": {"edited_text": edited_text, "updated_at": utc_now()}},
        )
        return result.matched_count == 1

    def start_analysis(self, meeting_id: str) -> None:
        self.meetings.update_one(
            {"_id": meeting_id},
            {
                "$set": {
                    "analysis_status": "processing",
                    "analysis_error": None,
                    "updated_at": utc_now(),
                }
            },
        )

    def save_analysis(self, meeting_id: str, analysis: dict) -> None:
        self.meetings.update_one(
            {"_id": meeting_id},
            {
                "$set": {
                    "analysis_status": "completed",
                    "analysis": analysis,
                    "analysis_error": None,
                    "analysis_updated_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
        )

    def fail_analysis(self, meeting_id: str, error: str) -> None:
        self.meetings.update_one(
            {"_id": meeting_id},
            {
                "$set": {
                    "analysis_status": "failed",
                    "analysis_error": error[:500],
                    "updated_at": utc_now(),
                }
            },
        )

    def list_meetings(self) -> list[dict]:
        result = []
        for meeting in self.meetings.find().sort("started_at", DESCENDING):
            result.append(self._serialize_meeting(meeting))
        return result

    def get_meeting(self, meeting_id: str) -> dict | None:
        meeting = self.meetings.find_one({"_id": meeting_id})
        if meeting is None:
            return None
        result = self._serialize_meeting(meeting)
        result["segments"] = [
            self._serialize_segment(segment)
            for segment in self.segments.find({"meeting_id": meeting_id}).sort(
                "sequence", ASCENDING
            )
        ]
        return result

    @staticmethod
    def _serialize_meeting(meeting: dict) -> dict:
        return {
            "id": meeting["_id"],
            "title": meeting["title"],
            "status": meeting["status"],
            "started_at": utc_isoformat(meeting["started_at"]),
            "ended_at": (
                utc_isoformat(meeting["ended_at"]) if meeting.get("ended_at") else None
            ),
            "duration_ms": meeting.get("duration_ms", 0),
            "audio_url": f"/api/meetings/{meeting['_id']}/audio",
            "analysis_status": meeting.get("analysis_status", "not_started"),
            "analysis": meeting.get("analysis"),
            "analysis_error": meeting.get("analysis_error"),
        }

    @staticmethod
    def _serialize_segment(segment: dict) -> dict:
        return {
            "id": segment["_id"],
            "meeting_id": segment["meeting_id"],
            "sequence": segment["sequence"],
            "speaker": segment["speaker"],
            "original_text": segment["original_text"],
            "text": segment.get("edited_text") or segment["text"],
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
        }
