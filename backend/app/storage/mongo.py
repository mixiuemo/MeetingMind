import os
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_isoformat(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
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
        self.speaker_profiles = database["speaker_profiles"]
        self.speeches = database["speeches"]
        self.chat_sessions = database["chat_sessions"]
        self.meetings.create_index([("started_at", DESCENDING)])
        self.segments.create_index(
            [("meeting_id", ASCENDING), ("sequence", ASCENDING)], unique=True
        )
        self.speaker_profiles.create_index([("name", ASCENDING)])
        self.speeches.create_index([("updated_at", DESCENDING)])
        self.chat_sessions.create_index([("updated_at", DESCENDING)])
        self.chat_sessions.create_index([("mode", ASCENDING), ("target_id", ASCENDING), ("updated_at", DESCENDING)])

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

    def create_speaker_profile(
        self, profile_id: str, name: str, embedding: list[float], sample: dict
    ) -> dict:
        now = utc_now()
        self.speaker_profiles.insert_one(
            {
                "_id": profile_id,
                "name": name,
                "centroid": embedding,
                "embeddings": [embedding],
                "samples": [sample],
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_speaker_profile(profile_id)

    def add_speaker_sample(
        self,
        profile_id: str,
        embedding: list[float],
        centroid: list[float],
        sample: dict,
    ) -> dict | None:
        result = self.speaker_profiles.update_one(
            {"_id": profile_id},
            {
                "$push": {"embeddings": embedding, "samples": sample},
                "$set": {"centroid": centroid, "updated_at": utc_now()},
            },
        )
        return self.get_speaker_profile(profile_id) if result.matched_count else None

    def list_speaker_profiles(self, include_embeddings: bool = False) -> list[dict]:
        return [
            self._serialize_speaker_profile(profile, include_embeddings)
            for profile in self.speaker_profiles.find().sort("updated_at", DESCENDING)
        ]

    def get_speaker_profile(
        self, profile_id: str, include_embeddings: bool = False
    ) -> dict | None:
        profile = self.speaker_profiles.find_one({"_id": profile_id})
        return (
            self._serialize_speaker_profile(profile, include_embeddings)
            if profile is not None
            else None
        )

    def delete_speaker_profile(self, profile_id: str) -> bool:
        return self.speaker_profiles.delete_one({"_id": profile_id}).deleted_count == 1

    def create_speech(self, speech_id: str, prompt: str, generated: dict) -> dict:
        now = utc_now()
        self.speeches.insert_one(
            {
                "_id": speech_id,
                "prompt": prompt,
                **generated,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_speech(speech_id)

    def list_speeches(self) -> list[dict]:
        return [
            self._serialize_speech(speech)
            for speech in self.speeches.find().sort("updated_at", DESCENDING)
        ]

    def get_speech(self, speech_id: str) -> dict | None:
        speech = self.speeches.find_one({"_id": speech_id})
        return self._serialize_speech(speech) if speech is not None else None

    def update_speech(self, speech_id: str, title: str, content: str, stats: dict) -> dict | None:
        result = self.speeches.update_one(
            {"_id": speech_id},
            {
                "$set": {
                    "title": title,
                    "content": content,
                    **stats,
                    "updated_at": utc_now(),
                }
            },
        )
        return self.get_speech(speech_id) if result.matched_count else None

    def regenerate_speech(self, speech_id: str, generated: dict) -> dict | None:
        result = self.speeches.update_one(
            {"_id": speech_id},
            {"$set": {**generated, "updated_at": utc_now()}},
        )
        return self.get_speech(speech_id) if result.matched_count else None

    def delete_speech(self, speech_id: str) -> bool:
        return self.speeches.delete_one({"_id": speech_id}).deleted_count == 1

    def create_chat_session(
        self,
        session_id: str,
        mode: str,
        target_id: str | None,
        title: str,
    ) -> dict:
        now = utc_now()
        self.chat_sessions.insert_one(
            {
                "_id": session_id,
                "mode": mode,
                "target_id": target_id,
                "title": title,
                "summary": None,
                "messages": [],
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_chat_session(session_id)

    def list_chat_sessions(self, mode: str | None = None, target_id: str | None = None) -> list[dict]:
        query = {}
        if mode:
            query["mode"] = mode
        if target_id is not None:
            query["target_id"] = target_id
        return [
            self._serialize_chat_session(session)
            for session in self.chat_sessions.find(query).sort("updated_at", DESCENDING)
        ]

    def get_chat_session(self, session_id: str) -> dict | None:
        session = self.chat_sessions.find_one({"_id": session_id})
        return self._serialize_chat_session(session) if session is not None else None

    def update_chat_session(self, session_id: str, *, summary: dict, messages: list[dict]) -> dict | None:
        result = self.chat_sessions.update_one(
            {"_id": session_id},
            {
                "$set": {
                    "summary": summary,
                    "messages": messages,
                    "updated_at": utc_now(),
                }
            },
        )
        return self.get_chat_session(session_id) if result.matched_count else None

    def delete_chat_session(self, session_id: str) -> bool:
        return self.chat_sessions.delete_one({"_id": session_id}).deleted_count == 1

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
            "speaker_id": segment.get("speaker_id", ""),
            "speaker_confidence": segment.get("speaker_confidence", 0),
            "speaker_status": segment.get("speaker_status", "disabled"),
            "speaker_profile_id": segment.get("speaker_profile_id", ""),
            "original_text": segment["original_text"],
            "text": segment.get("edited_text") or segment["text"],
            "start_ms": segment["start_ms"],
            "end_ms": segment["end_ms"],
        }

    @staticmethod
    def _serialize_speaker_profile(
        profile: dict, include_embeddings: bool = False
    ) -> dict:
        samples = [
            {
                "id": sample["id"],
                "duration_ms": sample["duration_ms"],
                "created_at": utc_isoformat(sample["created_at"]),
                "audio_url": (
                    f"/api/speakers/{profile['_id']}/samples/{sample['id']}/audio"
                ),
            }
            for sample in profile.get("samples", [])
        ]
        result = {
            "id": profile["_id"],
            "name": profile["name"],
            "sample_count": len(samples),
            "samples": samples,
            "created_at": utc_isoformat(profile["created_at"]),
            "updated_at": utc_isoformat(profile["updated_at"]),
        }
        if include_embeddings:
            result["centroid"] = profile.get("centroid", [])
            result["embeddings"] = profile.get("embeddings", [])
        return result

    @staticmethod
    def _serialize_speech(speech: dict) -> dict:
        return {
            "id": speech["_id"],
            "prompt": speech.get("prompt", ""),
            "title": speech.get("title", "未命名演讲稿"),
            "content": speech.get("content", ""),
            "word_count": speech.get("word_count", 0),
            "estimated_minutes": speech.get("estimated_minutes", 0),
            "created_at": utc_isoformat(speech["created_at"]),
            "updated_at": utc_isoformat(speech["updated_at"]),
        }

    @staticmethod
    def _serialize_chat_session(session: dict) -> dict:
        return {
            "id": session["_id"],
            "mode": session.get("mode", "free"),
            "target_id": session.get("target_id"),
            "title": session.get("title", "AI 助手"),
            "summary": session.get("summary"),
            "messages": [
                {
                    "id": message["id"],
                    "role": message["role"],
                    "content": message["content"],
                    "created_at": utc_isoformat(
                        message.get("created_at") or session["updated_at"]
                    ),
                }
                for message in session.get("messages", [])
            ],
            "created_at": utc_isoformat(session["created_at"]),
            "updated_at": utc_isoformat(session["updated_at"]),
        }
