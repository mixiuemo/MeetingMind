import os
import threading
from pathlib import Path

import numpy as np
import sherpa_onnx

from app.config import env_int


DEFAULT_MODEL_DIR = (
    Path(__file__).resolve().parents[2]
    / "models"
    / "sherpa-onnx-funasr-nano-int8-2025-12-30"
)


class FunASRNanoEngine:
    def __init__(self, model_dir: str | Path | None = None, num_threads: int | None = None):
        self.model_dir = Path(
            model_dir or os.getenv("HUIYI_ASR_MODEL_DIR") or DEFAULT_MODEL_DIR
        )
        self._validate_model_files()
        self._decode_lock = threading.Lock()
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_funasr_nano(
            encoder_adaptor=str(self.model_dir / "encoder_adaptor.int8.onnx"),
            llm=str(self.model_dir / "llm.int8.onnx"),
            embedding=str(self.model_dir / "embedding.int8.onnx"),
            tokenizer=str(self.model_dir / "Qwen3-0.6B"),
            language=os.getenv("HUIYI_ASR_LANGUAGE", "中文"),
            num_threads=num_threads or env_int("HUIYI_ASR_NUM_THREADS", 4),
        )

    def _validate_model_files(self) -> None:
        required = [
            "encoder_adaptor.int8.onnx",
            "llm.int8.onnx",
            "embedding.int8.onnx",
            "Qwen3-0.6B",
        ]
        missing = [name for name in required if not (self.model_dir / name).exists()]
        if missing:
            names = ", ".join(missing)
            raise FileNotFoundError(f"FunASR Nano 模型文件不完整: {names}")

    def transcribe(self, pcm_samples: list[int], sample_rate: int = 16_000) -> str:
        if not pcm_samples:
            return ""

        audio = np.asarray(pcm_samples, dtype=np.float32) / 32768.0
        with self._decode_lock:
            stream = self.recognizer.create_stream()
            stream.accept_waveform(sample_rate, audio)
            self.recognizer.decode_stream(stream)
            return stream.result.text.strip()
