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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.engines.funasr_nano import FunASRNanoEngine
from app.engines.silero_vad import SileroVadFactory
from app.exports.word import build_meeting_docx
from app.services.meeting_analysis import analyze_meeting, llm_enabled
from app.storage.audio import MeetingAudioWriter
from app.storage.mongo import MeetingRepository


SAMPLE_RATE = 16_000
PREVIEW_INTERVAL_MS = 800
MIN_PREVIEW_SAMPLES = int(SAMPLE_RATE * 0.8)
SILENCE_TO_FINAL_MS = 900
BLOCK_SEGMENT_MS = 6_000
OVERLAP_MS = 1_000
SPEECH_RMS_FALLBACK = 0.012
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
engine_error: str | None = None
repository: MeetingRepository | None = None
storage_error: str | None = None
analysis_tasks: set[asyncio.Task] = set()


@app.on_event("startup")
async def load_engines() -> None:
    global asr_engine, vad_factory, engine_error, repository, storage_error
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
    sequence: int = 0
    last_final_text: str = ""
    paused: bool = False

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
    text = await transcribe_samples(final_samples)
    clean_text = deduplicate_overlap(session.last_final_text, text)
    start_ms = milliseconds(session.segment_start_sample)
    end_ms = milliseconds(session.last_speech_sample or session.received_samples)
    if clean_text:
        segment = {
            "id": session.segment_id,
            "meeting_id": session.meeting_id,
            "sequence": session.sequence,
            "speaker": "发言人",
            "original_text": text,
            "text": clean_text,
            "edited_text": clean_text,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }
        if repository is not None:
            await asyncio.to_thread(repository.save_segment, segment)
        await websocket.send_json(
            {
                "type": "transcript.final",
                "segment_id": session.segment_id,
                "speaker": "发言人",
                "text": clean_text,
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
        session.last_final_text = f"{session.last_final_text}{clean_text}"[-100:]
        session.sequence += 1

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
    }


class SegmentUpdate(BaseModel):
    text: str


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
