import asyncio
import json
import os
import wave
from pathlib import Path

from websockets.asyncio.client import connect


TEST_WAV = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
    / "dia_yue.wav"
)


async def main() -> None:
    messages = []
    websocket_url = os.getenv(
        "HUIYI_TEST_WEBSOCKET_URL", "ws://127.0.0.1:8000/ws/meetings/live"
    )
    async with connect(websocket_url) as websocket:
        await websocket.send(
            json.dumps({"type": "meeting.start", "title": "端到端测试会议"})
        )
        messages.append(json.loads(await websocket.recv()))

        with wave.open(str(TEST_WAV), "rb") as wav_file:
            while chunk := wav_file.readframes(640):
                await websocket.send(chunk)
                await asyncio.sleep(0.01)

        for _ in range(20):
            await websocket.send(bytes(1_280))

        await websocket.send(json.dumps({"type": "meeting.end"}))
        while True:
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=30))
            messages.append(message)
            if message["type"] == "meeting.ended":
                break

    for message in messages:
        print(message)


if __name__ == "__main__":
    asyncio.run(main())
