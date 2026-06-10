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
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        samples = array("h")
        samples.frombytes(wav_file.readframes(wav_file.getnframes()))

    engine = FunASRNanoEngine()
    text = engine.transcribe(samples.tolist())
    assert text
    print(text)


if __name__ == "__main__":
    main()
