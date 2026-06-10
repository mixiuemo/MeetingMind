import wave
from pathlib import Path


class MeetingAudioWriter:
    def __init__(self, meeting_id: str):
        audio_dir = Path(__file__).resolve().parents[2] / "data" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        self.path = audio_dir / f"{meeting_id}.wav"
        self._wave = wave.open(str(self.path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(16_000)
        self._closed = False

    def write(self, chunk: bytes) -> None:
        if not self._closed:
            self._wave.writeframesraw(chunk)

    def close(self) -> None:
        if not self._closed:
            self._wave.close()
            self._closed = True
