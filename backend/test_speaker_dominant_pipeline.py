import asyncio
import wave
from pathlib import Path

import numpy as np

from app import main as meeting


TEST_WAV_DIR = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
)


class MessageCollector:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class AudioWriterStub:
    def write(self, chunk):
        pass

    def close(self):
        pass


def read_first_seconds(name: str, seconds: int) -> list[int]:
    with wave.open(str(TEST_WAV_DIR / name), "rb") as wav_file:
        samples = np.frombuffer(
            wav_file.readframes(seconds * meeting.SAMPLE_RATE), dtype=np.int16
        )
    return samples.tolist()


async def main() -> None:
    await meeting.load_engines()
    meeting.repository = None
    samples = read_first_seconds("dia_yue.wav", 4) + read_first_seconds("dia_sh.wav", 4)
    websocket = MessageCollector()
    session = meeting.AudioSession(
        vad=meeting.vad_factory.create(),
        audio_writer=AudioWriterStub(),
        speaker_tracker=meeting.speaker_engine.create_tracker(),
        segment_start_sample=0,
        last_speech_sample=len(samples),
        received_samples=len(samples),
        audio_samples=samples,
    )

    await meeting.send_final(websocket, session)
    finals = [
        message for message in websocket.messages if message["type"] == "transcript.final"
    ]
    assert len(finals) == 1
    assert finals[0]["speaker_id"]
    print("dominant speaker pipeline passed:", finals[0]["speaker"])


if __name__ == "__main__":
    asyncio.run(main())
