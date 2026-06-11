import wave
from pathlib import Path

import numpy as np

from app.engines.speaker import SpeakerEmbeddingEngine


TEST_WAV_DIR = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
)


def read_pcm(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnchannels() == 1
        return np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype=np.int16).tolist()


def main() -> None:
    engine = SpeakerEmbeddingEngine()
    tracker = engine.create_tracker()
    first = read_pcm(TEST_WAV_DIR / "dia_yue.wav")
    second = read_pcm(TEST_WAV_DIR / "dia_sh.wav")

    first_result = tracker.analyze(first, 0)
    second_result = tracker.analyze(second, 10_000)
    identity_tracker = engine.create_tracker(
        [
            {
                "id": "registered-speaker",
                "name": "测试用户",
                "centroid": tracker.clusters[first_result.speaker_id].centroid.tolist(),
            }
        ]
    )
    identity_result = identity_tracker.analyze(first, 0)
    preview_result = identity_tracker.identify_preview(first[: int(2.25 * 16_000)])

    assert first_result.speaker_id
    assert second_result.speaker_id
    assert first_result.speaker_id != second_result.speaker_id
    assert identity_result.speaker == "测试用户"
    assert identity_result.speaker_profile_id == "registered-speaker"
    assert preview_result.speaker == "测试用户"
    print("first:", first_result.as_dict())
    print("second:", second_result.as_dict())
    print("identity:", identity_result.as_dict())
    print("preview:", preview_result.as_dict())
    print("clusters:", list(tracker.clusters))


if __name__ == "__main__":
    main()
