import asyncio
from difflib import SequenceMatcher
import json
import re
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.engines.funasr_nano import FunASRNanoEngine
from app.engines.speaker import MeetingSpeakerTracker, SpeakerEmbeddingEngine
from app.engines.silero_vad import SileroVadFactory
from app.config import env_bool, env_float
from app.exports.word import build_meeting_docx
from app.exports.speech_word import build_speech_docx
from app.services.meeting_analysis import analyze_meeting, llm_enabled
from app.services.chat_assistant import generate_chat_reply, generate_chat_reply_stream
from app.services.speech_writer import (
    count_speech_characters,
    estimate_minutes,
    generate_speech,
    revise_speech,
)
from app.storage.audio import MeetingAudioWriter
from app.storage.mongo import MeetingRepository
from app.storage.mongo import utc_now
from app.storage.speakers import (
    delete_speaker_samples,
    save_speaker_sample,
    speaker_sample_path,
)


SAMPLE_RATE = 16_000
PREVIEW_INTERVAL_MS = 800
MIN_PREVIEW_SAMPLES = int(SAMPLE_RATE * 0.8)
SILENCE_TO_FINAL_MS = 900
BLOCK_SEGMENT_MS = 6_000
OVERLAP_MS = 1_000
SPEECH_RMS_FALLBACK = 0.012
SPEAKER_EARLY_IDENTIFY_SAMPLES = round(
    env_float("HUIYI_SPEAKER_EARLY_IDENTIFY_SECONDS", 2.25) * SAMPLE_RATE
)
PATHOLOGICAL_REPEAT_PATTERN = re.compile(r"(.{1,8}?)\1{4,}")


app = FastAPI(title="Huiyi Meeting Transcription API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

asr_engine: FunASRNanoEngine | None = None
vad_factory: SileroVadFactory | None = None
speaker_engine: SpeakerEmbeddingEngine | None = None
speaker_error: str | None = None
engine_error: str | None = None
repository: MeetingRepository | None = None
storage_error: str | None = None
analysis_tasks: set[asyncio.Task] = set()
STREAM_END = object()


def is_transient_executor_shutdown(error: Exception) -> bool:
    return isinstance(error, RuntimeError) and "Executor shutdown has been called" in str(error)


def next_stream_event(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return STREAM_END


@app.on_event("startup")
async def load_engines() -> None:
    global asr_engine, vad_factory, speaker_engine, speaker_error
    global engine_error, repository, storage_error
    try:
        asr_engine, vad_factory = await asyncio.gather(
            asyncio.to_thread(FunASRNanoEngine),
            asyncio.to_thread(SileroVadFactory),
        )
        engine_error = None
    except Exception as error:
        asr_engine = None
        vad_factory = None
        engine_error = str(error)
    if env_bool("HUIYI_SPEAKER_ENABLED", False):
        try:
            speaker_engine = await asyncio.to_thread(SpeakerEmbeddingEngine)
            speaker_error = None
        except Exception as error:
            speaker_engine = None
            speaker_error = str(error)
    else:
        speaker_engine = None
        speaker_error = None
    try:
        repository = await asyncio.to_thread(MeetingRepository)
        storage_error = None
    except Exception as error:
        repository = None
        storage_error = str(error)


async def run_meeting_analysis(meeting_id: str) -> None:
    if repository is None or not llm_enabled():
        return
    await asyncio.to_thread(repository.start_analysis, meeting_id)
    try:
        meeting = await asyncio.to_thread(repository.get_meeting, meeting_id)
        if meeting is None:
            raise RuntimeError("会议不存在")
        analysis = await asyncio.to_thread(analyze_meeting, meeting)
        await asyncio.to_thread(repository.save_analysis, meeting_id, analysis)
    except Exception as error:
        await asyncio.to_thread(repository.fail_analysis, meeting_id, str(error))


def schedule_meeting_analysis(meeting_id: str) -> None:
    if not llm_enabled():
        return
    task = asyncio.create_task(run_meeting_analysis(meeting_id))
    analysis_tasks.add(task)
    task.add_done_callback(analysis_tasks.discard)


@dataclass
class AudioSession:
    vad: object
    audio_writer: MeetingAudioWriter
    meeting_id: str = field(default_factory=lambda: str(uuid4()))
    segment_id: str = field(default_factory=lambda: str(uuid4()))
    received_samples: int = 0
    segment_start_sample: int | None = None
    last_speech_sample: int | None = None
    audio_samples: list[int] = field(default_factory=list)
    last_preview_sample: int = 0
    last_preview_text: str = ""
    preview_task: asyncio.Task | None = None
    speaker_preview_task: asyncio.Task | None = None
    speaker_preview_segment_id: str = ""
    sequence: int = 0
    last_final_text: str = ""
    paused: bool = False
    speaker_tracker: MeetingSpeakerTracker | None = None

    @property
    def position_ms(self) -> int:
        return milliseconds(self.received_samples)

    def reset_segment(self, overlap_samples: list[int] | None = None) -> None:
        self.segment_id = str(uuid4())
        self.audio_samples = overlap_samples or []
        self.segment_start_sample = (
            self.received_samples - len(self.audio_samples)
            if self.audio_samples
            else None
        )
        self.last_speech_sample = self.received_samples if self.audio_samples else None
        self.last_preview_text = ""
        self.speaker_preview_segment_id = ""


def milliseconds(sample_index: int) -> int:
    return round(sample_index / SAMPLE_RATE * 1000)


def pcm_rms(samples: array) -> float:
    if not samples:
        return 0.0
    waveform = np.asarray(samples, dtype=np.float32) / 32768.0
    return float(np.sqrt(np.mean(waveform * waveform)))


def deduplicate_overlap(previous: str, current: str) -> str:
    previous = previous.strip()
    current = current.strip()
    if not previous or not current:
        return current
    max_length = min(len(previous), len(current), 30)
    for length in range(max_length, 1, -1):
        if previous[-length:] == current[:length]:
            return current[length:].lstrip("，。！？、,.!? ")

    # 重叠音频的同一句话可能被识别成略有差异的文字，例如：
    # “从写代码出。” + “从写代码助手升级……”。匹配前段尾部与后段开头，
    # 仅在重复内容足够长且靠近双方边界时移除后段重复前缀。
    previous_tail = previous[-40:]
    current_head = current[:40]
    match = SequenceMatcher(None, previous_tail, current_head, autojunk=False).find_longest_match(
        0, len(previous_tail), 0, len(current_head)
    )
    previous_gap = len(previous_tail) - (match.a + match.size)
    is_strong_boundary_match = (
        match.size >= 4 and match.b <= 2 and previous_gap <= 3
    ) or (
        match.size >= 3 and match.b <= 1 and previous_gap <= 1
    )
    if is_strong_boundary_match:
        return current[match.b + match.size :].lstrip("，。！？、,.!? ")
    return current


def sanitize_transcript(text: str) -> str:
    """压缩离线大模型偶发的连续重复生成，同时保留正常的短暂口吃。"""
    cleaned = text.strip()
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = PATHOLOGICAL_REPEAT_PATTERN.sub(
            lambda match: match.group(1) * 3,
            cleaned,
        )
    return cleaned


async def transcribe_samples(samples: list[int]) -> str:
    if asr_engine is None:
        raise RuntimeError(engine_error or "sherpa-onnx 识别引擎尚未就绪")
    text = await asyncio.to_thread(asr_engine.transcribe, samples, SAMPLE_RATE)
    return sanitize_transcript(text)


async def send_preview(
    websocket: WebSocket,
    session: AudioSession,
    segment_id: str,
    samples: list[int],
    start_sample: int,
) -> None:
    text = await transcribe_samples(samples)
    if (
        session.segment_id != segment_id
        or not text
        or text == session.last_preview_text
    ):
        return
    session.last_preview_text = text
    await websocket.send_json(
        {
            "type": "transcript.preview",
            "segment_id": segment_id,
            "text": text,
            "start_ms": milliseconds(start_sample),
            "end_ms": session.position_ms,
        }
    )


async def send_speaker_preview(
    websocket: WebSocket,
    session: AudioSession,
    segment_id: str,
    samples: list[int],
) -> None:
    if session.speaker_tracker is None:
        return
    analysis = await asyncio.to_thread(session.speaker_tracker.identify_preview, samples)
    if session.segment_id != segment_id or analysis.status != "recognized":
        return
    session.speaker_preview_segment_id = segment_id
    await websocket.send_json(
        {
            "type": "speaker.preview",
            "segment_id": segment_id,
            **analysis.as_dict(),
        }
    )


def log_preview_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        print(f"预览识别失败: {error}")


def schedule_preview(websocket: WebSocket, session: AudioSession) -> None:
    if session.preview_task is not None and not session.preview_task.done():
        return
    if (
        session.segment_start_sample is None
        or len(session.audio_samples) < MIN_PREVIEW_SAMPLES
    ):
        return
    session.preview_task = asyncio.create_task(
        send_preview(
            websocket,
            session,
            session.segment_id,
            session.audio_samples.copy(),
            session.segment_start_sample,
        )
    )
    session.preview_task.add_done_callback(log_preview_error)


def schedule_speaker_preview(websocket: WebSocket, session: AudioSession) -> None:
    if session.speaker_tracker is None:
        return
    if len(session.audio_samples) < SPEAKER_EARLY_IDENTIFY_SAMPLES:
        return
    if session.speaker_preview_segment_id == session.segment_id:
        return
    if session.speaker_preview_task is not None and not session.speaker_preview_task.done():
        return
    session.speaker_preview_task = asyncio.create_task(
        send_speaker_preview(
            websocket,
            session,
            session.segment_id,
            session.audio_samples[-SPEAKER_EARLY_IDENTIFY_SAMPLES:].copy(),
        )
    )
    session.speaker_preview_task.add_done_callback(log_preview_error)


async def save_and_send_segment(
    websocket: WebSocket,
    session: AudioSession,
    segment_id: str,
    text: str,
    original_text: str,
    start_ms: int,
    end_ms: int,
    speaker_data: dict,
) -> None:
    speaker_name = speaker_data.get("speaker", "发言人")
    segment = {
        "id": segment_id,
        "meeting_id": session.meeting_id,
        "sequence": session.sequence,
        "speaker": speaker_name,
        "original_text": original_text,
        "text": text,
        "edited_text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        **speaker_data,
    }
    if repository is not None:
        await asyncio.to_thread(repository.save_segment, segment)
    await websocket.send_json(
        {
            "type": "transcript.final",
            "segment_id": segment_id,
            "speaker": speaker_name,
            "speaker_id": speaker_data.get("speaker_id", ""),
            "speaker_confidence": speaker_data.get("speaker_confidence", 0),
            "speaker_status": speaker_data.get("speaker_status", "disabled"),
            "speaker_profile_id": speaker_data.get("speaker_profile_id", ""),
            "text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
    )
    session.last_final_text = f"{session.last_final_text}{text}"[-100:]
    session.sequence += 1


async def send_final(
    websocket: WebSocket, session: AudioSession, retain_overlap: bool = False
) -> None:
    if session.segment_start_sample is None or not session.audio_samples:
        return
    if session.preview_task is not None and not session.preview_task.done():
        try:
            await session.preview_task
        except Exception:
            pass

    final_samples = session.audio_samples.copy()
    # 预览只负责低延迟展示；积木结算必须重新识别完整音频块，
    # 避免将预览阶段的缺字、误识别或重复生成永久写入会议记录。
    speaker_task = (
        asyncio.to_thread(
            session.speaker_tracker.analyze,
            final_samples,
            milliseconds(session.segment_start_sample),
        )
        if session.speaker_tracker is not None
        else None
    )
    if speaker_task is None:
        text = await transcribe_samples(final_samples)
        speaker_analysis = None
    else:
        text, speaker_analysis = await asyncio.gather(
            transcribe_samples(final_samples), speaker_task
        )
    start_ms = milliseconds(session.segment_start_sample)
    end_ms = milliseconds(session.last_speech_sample or session.received_samples)
    clean_text = deduplicate_overlap(session.last_final_text, text)
    if clean_text:
        speaker_data = speaker_analysis.as_dict() if speaker_analysis else {}
        await save_and_send_segment(
            websocket,
            session,
            session.segment_id,
            clean_text,
            text,
            start_ms,
            end_ms,
            speaker_data,
        )

    overlap = (
        final_samples[-(OVERLAP_MS * SAMPLE_RATE // 1000) :]
        if retain_overlap
        else None
    )
    session.reset_segment(overlap)


async def process_audio(websocket: WebSocket, session: AudioSession, chunk: bytes) -> None:
    samples = array("h")
    samples.frombytes(chunk)
    if not samples:
        return

    session.received_samples += len(samples)
    session.audio_writer.write(chunk)
    if session.paused:
        return

    waveform = np.asarray(samples, dtype=np.float32) / 32768.0
    session.vad.accept_waveform(waveform)
    has_speech_energy = pcm_rms(samples) >= SPEECH_RMS_FALLBACK
    is_speech = has_speech_energy or (
        session.segment_start_sample is not None and session.vad.is_speech_detected()
    )

    if is_speech:
        if session.segment_start_sample is None:
            session.segment_start_sample = session.received_samples - len(samples)
        session.last_speech_sample = session.received_samples
        session.audio_samples.extend(samples)

        if (
            session.received_samples - session.last_preview_sample
            >= PREVIEW_INTERVAL_MS * SAMPLE_RATE // 1000
        ):
            session.last_preview_sample = session.received_samples
            schedule_preview(websocket, session)
            schedule_speaker_preview(websocket, session)

        if len(session.audio_samples) >= BLOCK_SEGMENT_MS * SAMPLE_RATE // 1000:
            await send_final(websocket, session, retain_overlap=True)
        return

    if session.segment_start_sample is None:
        return

    session.audio_samples.extend(samples)
    silence_samples = session.received_samples - (session.last_speech_sample or 0)
    if silence_samples >= SILENCE_TO_FINAL_MS * SAMPLE_RATE // 1000:
        await send_final(websocket, session)


async def process_command(websocket: WebSocket, session: AudioSession, text: str) -> bool:
    message = json.loads(text)
    message_type = message.get("type")

    if message_type == "meeting.start":
        title = str(message.get("title") or "未命名会议")
        if repository is not None:
            await asyncio.to_thread(
                repository.create_meeting,
                session.meeting_id,
                title,
                str(session.audio_writer.path),
            )
        await websocket.send_json(
            {"type": "meeting.started", "meeting_id": session.meeting_id}
        )
    elif message_type == "meeting.pause":
        await send_final(websocket, session)
        session.paused = True
    elif message_type == "meeting.resume":
        session.paused = False
    elif message_type == "meeting.end":
        await send_final(websocket, session)
        session.audio_writer.close()
        if repository is not None:
            await asyncio.to_thread(
                repository.finish_meeting, session.meeting_id, session.position_ms
            )
            schedule_meeting_analysis(session.meeting_id)
        await websocket.send_json({"type": "meeting.ended"})
        return False
    else:
        await websocket.send_json(
            {"type": "error", "message": f"不支持的消息类型: {message_type}"}
        )
    return True


@app.get("/api/health")
async def health() -> dict[str, str]:
    if asr_engine is None or vad_factory is None:
        return {
            "status": "error",
            "asr_engine": "funasr-nano",
            "vad_engine": "silero-vad",
            "message": engine_error or "",
        }
    return {
        "status": "ok",
        "asr_engine": "funasr-nano",
        "vad_engine": "silero-vad",
        "asr_model_dir": str(asr_engine.model_dir),
        "vad_model_path": str(vad_factory.model_path),
        "storage": "mongodb" if repository is not None else f"error: {storage_error}",
        "llm": "enabled" if llm_enabled() else "disabled",
        "speaker": (
            "enabled"
            if speaker_engine is not None
            else f"disabled: {speaker_error}" if speaker_error else "disabled"
        ),
        "speaker_model_path": (
            str(speaker_engine.model_path) if speaker_engine is not None else ""
        ),
    }


class SegmentUpdate(BaseModel):
    text: str


class SpeechGenerateRequest(BaseModel):
    prompt: str


class SpeechUpdate(BaseModel):
    title: str
    content: str


class SpeechRevisionRequest(BaseModel):
    instruction: str
    session_id: str | None = None


class ChatSessionCreateRequest(BaseModel):
    mode: str
    target_id: str | None = None
    title: str | None = None


class ChatMessageRequest(BaseModel):
    content: str


def normalize_chat_mode(mode: str) -> str:
    clean_mode = mode.strip().lower()
    if clean_mode not in {"free", "meeting", "speech"}:
        raise HTTPException(status_code=400, detail="不支持的助手模式")
    return clean_mode


async def load_chat_target(mode: str, target_id: str | None) -> tuple[dict | None, dict | None]:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    if mode == "meeting":
        if not target_id:
            raise HTTPException(status_code=400, detail="会议助手需要 target_id")
        meeting = await asyncio.to_thread(repository.get_meeting, target_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="会议不存在")
        return meeting, None
    if mode == "speech":
        if not target_id:
            raise HTTPException(status_code=400, detail="演讲稿助手需要 target_id")
        speech = await asyncio.to_thread(repository.get_speech, target_id)
        if speech is None:
            raise HTTPException(status_code=404, detail="演讲稿不存在")
        return None, speech
    return None, None


def default_chat_title(mode: str, meeting: dict | None, speech: dict | None) -> str:
    if mode == "meeting":
        return f"会议助手：{meeting.get('title') or '未命名会议'}"
    if mode == "speech":
        return f"演讲稿助手：{speech.get('title') or '未命名演讲稿'}"
    return "自由聊天"


@app.get("/api/meetings")
async def list_meetings() -> list[dict]:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    return await asyncio.to_thread(repository.list_meetings)


@app.get("/api/meetings/{meeting_id}")
async def get_meeting(meeting_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    meeting = await asyncio.to_thread(repository.get_meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    return meeting


@app.patch("/api/meetings/{meeting_id}/segments/{segment_id}")
async def update_segment(meeting_id: str, segment_id: str, update: SegmentUpdate) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    updated = await asyncio.to_thread(
        repository.update_segment, meeting_id, segment_id, update.text
    )
    if not updated:
        raise HTTPException(status_code=404, detail="转写段落不存在")
    return {"status": "ok"}


@app.get("/api/meetings/{meeting_id}/audio")
async def get_meeting_audio(meeting_id: str):
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    meeting = await asyncio.to_thread(repository.get_meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    audio_path = Path(__file__).resolve().parents[1] / "data" / "audio" / f"{meeting_id}.wav"
    if not audio_path.is_file():
        raise HTTPException(status_code=404, detail="会议音频不存在")
    return FileResponse(audio_path, media_type="audio/wav", filename=f"{meeting_id}.wav")


def prepare_speaker_sample(pcm_bytes: bytes) -> tuple[list[int], int]:
    if len(pcm_bytes) % 2:
        raise HTTPException(status_code=400, detail="PCM16 音频字节长度无效")
    samples = array("h")
    samples.frombytes(pcm_bytes)
    duration_ms = milliseconds(len(samples))
    if duration_ms < 3_000:
        raise HTTPException(status_code=400, detail="声纹样本至少需要 3 秒有效录音")
    if duration_ms > 30_000:
        raise HTTPException(status_code=400, detail="声纹样本不能超过 30 秒")
    return samples.tolist(), duration_ms


async def extract_speaker_sample(pcm_bytes: bytes) -> tuple[list[float], int]:
    if speaker_engine is None:
        raise HTTPException(status_code=503, detail=speaker_error or "声纹引擎未启用")
    samples, duration_ms = prepare_speaker_sample(pcm_bytes)
    embedding = await asyncio.to_thread(speaker_engine.extract, samples)
    if embedding is None:
        raise HTTPException(status_code=400, detail="录音质量不足，请靠近麦克风重新录制")
    return embedding.tolist(), duration_ms


@app.get("/api/speakers")
async def list_speaker_profiles() -> list[dict]:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    return await asyncio.to_thread(repository.list_speaker_profiles)


@app.post("/api/speakers")
async def create_speaker_profile(name: str, request: Request) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 40:
        raise HTTPException(status_code=400, detail="请输入 1 到 40 个字符的姓名")
    pcm_bytes = await request.body()
    embedding, duration_ms = await extract_speaker_sample(pcm_bytes)
    profile_id = str(uuid4())
    sample_id = str(uuid4())
    path = await asyncio.to_thread(
        save_speaker_sample, profile_id, sample_id, pcm_bytes
    )
    sample = {
        "id": sample_id,
        "duration_ms": duration_ms,
        "audio_path": str(path),
        "created_at": utc_now(),
    }
    return await asyncio.to_thread(
        repository.create_speaker_profile,
        profile_id,
        clean_name,
        embedding,
        sample,
    )


@app.post("/api/speakers/{profile_id}/samples")
async def add_speaker_profile_sample(profile_id: str, request: Request) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    profile = await asyncio.to_thread(
        repository.get_speaker_profile, profile_id, True
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="声纹身份不存在")
    pcm_bytes = await request.body()
    embedding, duration_ms = await extract_speaker_sample(pcm_bytes)
    embeddings = [
        np.asarray(item, dtype=np.float32)
        for item in [*profile.get("embeddings", []), embedding]
    ]
    centroid = np.mean(embeddings, axis=0)
    centroid /= np.linalg.norm(centroid)
    sample_id = str(uuid4())
    path = await asyncio.to_thread(
        save_speaker_sample, profile_id, sample_id, pcm_bytes
    )
    sample = {
        "id": sample_id,
        "duration_ms": duration_ms,
        "audio_path": str(path),
        "created_at": utc_now(),
    }
    return await asyncio.to_thread(
        repository.add_speaker_sample,
        profile_id,
        embedding,
        centroid.tolist(),
        sample,
    )


@app.get("/api/speakers/{profile_id}/samples/{sample_id}/audio")
async def get_speaker_sample_audio(profile_id: str, sample_id: str):
    path = speaker_sample_path(profile_id, sample_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="声纹样本音频不存在")
    return FileResponse(path, media_type="audio/wav", filename=f"{sample_id}.wav")


@app.delete("/api/speakers/{profile_id}")
async def delete_speaker_profile(profile_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    deleted = await asyncio.to_thread(repository.delete_speaker_profile, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="声纹身份不存在")
    await asyncio.to_thread(delete_speaker_samples, profile_id)
    return {"status": "ok"}


@app.get("/api/speeches")
async def list_speeches() -> list[dict]:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    return await asyncio.to_thread(repository.list_speeches)


@app.get("/api/chat/sessions")
async def list_chat_sessions(mode: str | None = None, target_id: str | None = None) -> list[dict]:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    normalized_mode = normalize_chat_mode(mode) if mode else None
    return await asyncio.to_thread(repository.list_chat_sessions, normalized_mode, target_id)


@app.post("/api/chat/sessions")
async def create_chat_session(request: ChatSessionCreateRequest) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    mode = normalize_chat_mode(request.mode)
    meeting, speech = await load_chat_target(mode, request.target_id)
    title = (request.title or "").strip() or default_chat_title(mode, meeting, speech)
    return await asyncio.to_thread(
        repository.create_chat_session,
        str(uuid4()),
        mode,
        request.target_id,
        title[:120],
    )


@app.get("/api/chat/sessions/{session_id}")
async def get_chat_session(session_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    session = await asyncio.to_thread(repository.get_chat_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    return session


@app.post("/api/chat/sessions/{session_id}/messages")
async def send_chat_message(session_id: str, request: ChatMessageRequest) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    session = await asyncio.to_thread(repository.get_chat_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    meeting, speech = await load_chat_target(session["mode"], session.get("target_id"))
    try:
        result = await asyncio.to_thread(
            generate_chat_reply,
            session["mode"],
            request.content,
            summary=session.get("summary"),
            messages=session.get("messages", []),
            meeting=meeting,
            speech=speech,
        )
    except RuntimeError as error:
        if is_transient_executor_shutdown(error):
            raise HTTPException(status_code=503, detail="服务正在重启，请稍后重试") from error
        raise HTTPException(status_code=502, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    now = utc_now()
    persisted_messages = []
    for message in result["messages"]:
        persisted_messages.append(
            {
                **message,
                "created_at": now,
            }
        )
    updated = await asyncio.to_thread(
        repository.update_chat_session,
        session_id,
        summary=result["summary"],
        messages=persisted_messages,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    return {
        "session": updated,
        "message": updated["messages"][-1] if updated["messages"] else None,
    }


@app.post("/api/chat/sessions/{session_id}/messages/stream")
async def send_chat_message_stream(session_id: str, request: ChatMessageRequest):
    """流式聊天消息端点"""
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")

    session = await asyncio.to_thread(repository.get_chat_session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="助手会话不存在")

    meeting, speech = await load_chat_target(session["mode"], session.get("target_id"))

    async def event_generator():
        try:
            result_data = None
            iterator = generate_chat_reply_stream(
                session["mode"],
                request.content,
                summary=session.get("summary"),
                messages=session.get("messages", []),
                meeting=meeting,
                speech=speech,
            )
            while True:
                event = await asyncio.to_thread(next_stream_event, iterator)
                if event is STREAM_END:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                if event.get("type") == "done":
                    result_data = event

            if result_data:
                now = utc_now()
                persisted_messages = []
                for message in result_data["messages"]:
                    persisted_messages.append({**message, "created_at": now})

                await asyncio.to_thread(
                    repository.update_chat_session,
                    session_id,
                    summary=result_data["summary"],
                    messages=persisted_messages,
                )
        except Exception as error:
            error_event = {
                "type": "error",
                "error": str(error),
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Encoding": "identity",
            "X-Accel-Buffering": "no",
        },
    )


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    deleted = await asyncio.to_thread(repository.delete_chat_session, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="助手会话不存在")
    return {"status": "ok"}


@app.post("/api/speeches")
async def create_speech(request: SpeechGenerateRequest) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入演讲稿需求描述")
    try:
        generated = await asyncio.to_thread(generate_speech, prompt)
    except RuntimeError as error:
        if is_transient_executor_shutdown(error):
            raise HTTPException(status_code=503, detail="服务正在重启，请稍后重试") from error
        raise HTTPException(status_code=502, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    try:
        return await asyncio.to_thread(
            repository.create_speech, str(uuid4()), prompt, generated
        )
    except RuntimeError as error:
        if is_transient_executor_shutdown(error):
            raise HTTPException(status_code=503, detail="服务正在重启，请稍后重试") from error
        raise


@app.get("/api/speeches/{speech_id}")
async def get_speech(speech_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    speech = await asyncio.to_thread(repository.get_speech, speech_id)
    if speech is None:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    return speech


@app.patch("/api/speeches/{speech_id}")
async def update_speech(speech_id: str, update: SpeechUpdate) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    title = update.title.strip() or "未命名演讲稿"
    content = update.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="演讲稿正文不能为空")
    stats = {
        "word_count": count_speech_characters(content),
        "estimated_minutes": estimate_minutes(content),
    }
    speech = await asyncio.to_thread(
        repository.update_speech, speech_id, title[:100], content, stats
    )
    if speech is None:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    return speech


@app.post("/api/speeches/{speech_id}/revise")
async def revise_selected_speech(speech_id: str, request: SpeechRevisionRequest) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    speech = await asyncio.to_thread(repository.get_speech, speech_id)
    if speech is None:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    try:
        revised = await asyncio.to_thread(revise_speech, speech, request.instruction)
    except RuntimeError as error:
        if is_transient_executor_shutdown(error):
            raise HTTPException(status_code=503, detail="服务正在重启，请稍后重试") from error
        raise HTTPException(status_code=502, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    updated = await asyncio.to_thread(
        repository.update_speech,
        speech_id,
        revised["title"],
        revised["content"],
        {
            "word_count": revised["word_count"],
            "estimated_minutes": revised["estimated_minutes"],
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    updated_session = None
    if request.session_id:
        session = await asyncio.to_thread(repository.get_chat_session, request.session_id)
        if (
            session is None
            or session.get("mode") != "speech"
            or session.get("target_id") != speech_id
        ):
            raise HTTPException(status_code=400, detail="演讲稿助手会话与当前演讲稿不匹配")
        now = utc_now()
        messages = [
            *session.get("messages", []),
            {
                "id": str(uuid4()),
                "role": "user",
                "content": request.instruction.strip(),
                "created_at": now,
            },
            {
                "id": str(uuid4()),
                "role": "assistant",
                "content": revised["message"],
                "created_at": now,
            },
        ]
        updated_session = await asyncio.to_thread(
            repository.update_chat_session,
            request.session_id,
            summary=session.get("summary"),
            messages=messages,
        )
    return {
        "speech": updated,
        "message": revised["message"],
        "revision": revised["revision"],
        "session": updated_session,
    }


@app.post("/api/speeches/{speech_id}/regenerate")
async def regenerate_speech(speech_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    speech = await asyncio.to_thread(repository.get_speech, speech_id)
    if speech is None:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    try:
        generated = await asyncio.to_thread(generate_speech, speech["prompt"])
    except RuntimeError as error:
        if is_transient_executor_shutdown(error):
            raise HTTPException(status_code=503, detail="服务正在重启，请稍后重试") from error
        raise HTTPException(status_code=502, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    try:
        return await asyncio.to_thread(repository.regenerate_speech, speech_id, generated)
    except RuntimeError as error:
        if is_transient_executor_shutdown(error):
            raise HTTPException(status_code=503, detail="服务正在重启，请稍后重试") from error
        raise


@app.delete("/api/speeches/{speech_id}")
async def delete_speech(speech_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    deleted = await asyncio.to_thread(repository.delete_speech, speech_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    return {"status": "ok"}


@app.get("/api/speeches/{speech_id}/export/docx")
async def export_speech_docx(speech_id: str):
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    speech = await asyncio.to_thread(repository.get_speech, speech_id)
    if speech is None:
        raise HTTPException(status_code=404, detail="演讲稿不存在")
    document = await asyncio.to_thread(build_speech_docx, speech)
    filename = quote(f"{speech['title'] or '演讲稿'}.docx")
    return StreamingResponse(
        document,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/meetings/{meeting_id}/export/docx")
async def export_meeting_docx(meeting_id: str):
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    meeting = await asyncio.to_thread(repository.get_meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    document = await asyncio.to_thread(build_meeting_docx, meeting)
    filename = quote(f"{meeting['title'] or '会议记录'}.docx")
    return StreamingResponse(
        document,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.post("/api/meetings/{meeting_id}/analysis")
async def generate_meeting_analysis(meeting_id: str) -> dict:
    if repository is None:
        raise HTTPException(status_code=503, detail=storage_error or "MongoDB不可用")
    if not llm_enabled():
        raise HTTPException(status_code=503, detail="LLM 分析未启用")
    meeting = await asyncio.to_thread(repository.get_meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    if meeting.get("analysis_status") == "processing":
        return {"status": "processing"}
    schedule_meeting_analysis(meeting_id)
    return {"status": "processing"}


@app.websocket("/ws/meetings/live")
async def live_meeting(websocket: WebSocket) -> None:
    await websocket.accept()
    if asr_engine is None or vad_factory is None:
        await websocket.send_json(
            {"type": "error", "message": engine_error or "语音引擎尚未就绪"}
        )
        await websocket.close()
        return

    if repository is None:
        await websocket.send_json(
            {"type": "error", "message": storage_error or "MongoDB存储不可用"}
        )
        await websocket.close()
        return

    meeting_id = str(uuid4())
    session = AudioSession(
        vad=vad_factory.create(),
        meeting_id=meeting_id,
        audio_writer=MeetingAudioWriter(meeting_id),
        speaker_tracker=(
            speaker_engine.create_tracker(
                await asyncio.to_thread(
                    repository.list_speaker_profiles, True
                )
            )
            if speaker_engine is not None
            else None
        ),
    )
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await process_audio(websocket, session, message["bytes"])
            elif message.get("text") is not None:
                should_continue = await process_command(
                    websocket, session, message["text"]
                )
                if not should_continue:
                    await websocket.close()
                    return
    except WebSocketDisconnect:
        pass
    except (json.JSONDecodeError, ValueError) as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        await websocket.close()
    finally:
        session.audio_writer.close()
        if repository is not None:
            await asyncio.to_thread(
                repository.finish_meeting, session.meeting_id, session.position_ms
            )
