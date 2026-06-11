import asyncio
import wave
from pathlib import Path

from starlette.requests import Request

from app import main as meeting


TEST_WAV = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
    / "dia_yue.wav"
)


def pcm_request(body: bytes) -> Request:
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


async def main() -> None:
    await meeting.load_engines()
    with wave.open(str(TEST_WAV), "rb") as wav_file:
        pcm_bytes = wav_file.readframes(wav_file.getnframes())

    profile = await meeting.create_speaker_profile("API测试身份", pcm_request(pcm_bytes))
    profile_id = profile["id"]
    try:
        profiles = await meeting.list_speaker_profiles()
        assert any(item["id"] == profile_id for item in profiles)

        profile = await meeting.add_speaker_profile_sample(
            profile_id, pcm_request(pcm_bytes)
        )
        assert profile["sample_count"] == 2

        sample_path = meeting.speaker_sample_path(
            profile_id, profile["samples"][0]["id"]
        )
        assert sample_path.is_file()
        print(f"speaker profile API passed: {profile_id}")
    finally:
        await meeting.delete_speaker_profile(profile_id)


if __name__ == "__main__":
    asyncio.run(main())
