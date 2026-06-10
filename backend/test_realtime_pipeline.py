import asyncio
import wave
from pathlib import Path

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
    session = meeting.AudioSession(
        vad=meeting.vad_factory.create(),
        audio_writer=AudioWriterStub(),
    )

    with wave.open(str(TEST_WAV), "rb") as wav_file:
        while chunk := wav_file.readframes(1_365):
            await meeting.process_audio(websocket, session, chunk)

    for _ in range(12):
        await meeting.process_audio(websocket, session, bytes(2_730))
    await meeting.send_final(websocket, session)

    finals = [
        message["text"]
        for message in websocket.messages
        if message["type"] == "transcript.final"
    ]
    assert len(finals) >= 2
    print(f"final blocks: {len(finals)}")
    for text in finals:
        print(text)


if __name__ == "__main__":
    asyncio.run(main())
