import wave
from pathlib import Path

import numpy as np

from app.engines.funasr_nano import FunASRNanoEngine
from app.engines.silero_vad import SileroVadFactory


TEST_WAV = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
    / "dia_yue.wav"
)


def main() -> None:
    vad = SileroVadFactory().create()

    with wave.open(str(TEST_WAV), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        while data := wav_file.readframes(512):
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
            vad.accept_waveform(samples)

    vad.flush()
    segments = []
    while not vad.empty():
        front = vad.front
        segments.append((front.start, list(front.samples)))
        vad.pop()

    assert segments
    engine = FunASRNanoEngine()
    text = engine.transcribe(
        (np.asarray(segments[0][1]) * 32768).astype(np.int16).tolist()
    )
    assert text
    print(f"VAD segments: {len(segments)}")
    print(text)


if __name__ == "__main__":
    main()
