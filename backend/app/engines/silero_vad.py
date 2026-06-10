import os
from pathlib import Path

import sherpa_onnx

from app.config import env_float


DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "silero_vad.onnx"
)


class SileroVadFactory:
    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(
            model_path or os.getenv("HUIYI_VAD_MODEL_PATH") or DEFAULT_MODEL_PATH
        )
        if not self.model_path.is_file():
            raise FileNotFoundError(f"Silero VAD 模型不存在: {self.model_path}")

    def create(self) -> sherpa_onnx.VoiceActivityDetector:
        config = sherpa_onnx.VadModelConfig(
            silero_vad=sherpa_onnx.SileroVadModelConfig(
                model=str(self.model_path),
                threshold=env_float("HUIYI_VAD_THRESHOLD", 0.45),
                min_silence_duration=env_float(
                    "HUIYI_VAD_MIN_SILENCE_SECONDS", 0.85
                ),
                min_speech_duration=env_float(
                    "HUIYI_VAD_MIN_SPEECH_SECONDS", 0.25
                ),
                max_speech_duration=env_float(
                    "HUIYI_VAD_MAX_SPEECH_SECONDS", 15
                ),
            ),
            sample_rate=16_000,
        )
        return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
