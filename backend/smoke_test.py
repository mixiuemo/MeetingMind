import asyncio
import json

from websockets.asyncio.client import connect


async def main() -> None:
    async with connect("ws://127.0.0.1:8000/ws/meetings/live") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "meeting.start",
                    "sample_rate": 16_000,
                    "channels": 1,
                    "format": "pcm_s16le",
                }
            )
        )
        started = json.loads(await websocket.recv())
        assert started["type"] == "meeting.started"

        await websocket.send(json.dumps({"type": "meeting.end"}))
        ended = json.loads(await asyncio.wait_for(websocket.recv(), timeout=2))
        assert ended["type"] == "meeting.ended"

    print("WebSocket control protocol smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
