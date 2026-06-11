import wave
import shutil
from pathlib import Path


SPEAKER_AUDIO_DIR = Path(__file__).resolve().parents[2] / "data" / "speaker_samples"


def save_speaker_sample(profile_id: str, sample_id: str, pcm_bytes: bytes) -> Path:
    profile_dir = SPEAKER_AUDIO_DIR / profile_id
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / f"{sample_id}.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(pcm_bytes)
    return path


def speaker_sample_path(profile_id: str, sample_id: str) -> Path:
    return SPEAKER_AUDIO_DIR / profile_id / f"{sample_id}.wav"


def delete_speaker_samples(profile_id: str) -> None:
    shutil.rmtree(SPEAKER_AUDIO_DIR / profile_id, ignore_errors=True)
