import asyncio
import wave
from pathlib import Path

import numpy as np

from app import main as meeting


TEST_WAV = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
    / "dia_yue.wav"
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


async def main() -> None:
    await meeting.load_engines()
    websocket = MessageCollector()
    with wave.open(str(TEST_WAV), "rb") as wav_file:
        registered_samples = np.frombuffer(
            wav_file.readframes(wav_file.getnframes()), dtype=np.int16
        )
    registered_embedding = meeting.speaker_engine.extract(registered_samples)
    session = meeting.AudioSession(
        vad=meeting.vad_factory.create(),
        audio_writer=AudioWriterStub(),
        speaker_tracker=meeting.speaker_engine.create_tracker(
            [
                {
                    "id": "registered-speaker",
                    "name": "测试用户",
                    "centroid": registered_embedding.tolist(),
                }
            ]
        ),
    )

    with wave.open(str(TEST_WAV), "rb") as wav_file:
        while chunk := wav_file.readframes(1_365):
            await meeting.process_audio(websocket, session, chunk)

    for _ in range(12):
        await meeting.process_audio(websocket, session, bytes(2_730))
    await meeting.send_final(websocket, session)

    finals = [
        message
        for message in websocket.messages
        if message["type"] == "transcript.final"
    ]
    assert len(finals) >= 2
    assert all(message.get("speaker_id") for message in finals)
    assert all(message.get("speaker_status") for message in finals)
    speaker_previews = [
        message
        for message in websocket.messages
        if message["type"] == "speaker.preview"
    ]
    assert speaker_previews
    assert speaker_previews[0]["speaker"] == "测试用户"
    print(f"early speaker preview: {speaker_previews[0]['speaker']}")
    print(f"final blocks: {len(finals)}")
    for message in finals:
        print(message["speaker"], message["speaker_status"], message["text"])


if __name__ == "__main__":
    asyncio.run(main())
