import time
import wave
from array import array
from pathlib import Path

from app.engines.funasr_nano import FunASRNanoEngine


TEST_WAV = (
    Path(__file__).resolve().parent
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
    / "test_wavs"
    / "dia_yue.wav"
)


def main() -> None:
    with wave.open(str(TEST_WAV), "rb") as wav_file:
        samples = array("h")
        samples.frombytes(wav_file.readframes(wav_file.getnframes()))

    engine = FunASRNanoEngine()
    for seconds in (1, 2, 4, 8):
        audio = samples[: seconds * 16_000]
        started = time.perf_counter()
        text = engine.transcribe(audio.tolist())
        elapsed = time.perf_counter() - started
        print(f"{seconds}s audio -> {elapsed:.3f}s, text={text[:30]!r}")


if __name__ == "__main__":
    main()
