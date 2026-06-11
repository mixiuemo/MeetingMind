import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx

from app.config import env_float, env_int


DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "models"
    / "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
)
SAMPLE_RATE = 16_000


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


@dataclass
class SpeakerCluster:
    centroid: np.ndarray
    sample_count: int = 1

    def update(self, embedding: np.ndarray) -> None:
        combined = self.centroid * self.sample_count + embedding
        norm = float(np.linalg.norm(combined))
        if norm > 0:
            self.centroid = combined / norm
        self.sample_count += 1


@dataclass
class SpeakerWindow:
    start_ms: int
    end_ms: int
    speaker_id: str
    similarity: float


@dataclass
class SpeakerAnalysis:
    speaker_id: str
    speaker: str
    confidence: float
    status: str
    speaker_profile_id: str = ""

    def as_dict(self) -> dict:
        return {
            "speaker_id": self.speaker_id,
            "speaker": self.speaker,
            "speaker_confidence": round(self.confidence, 4),
            "speaker_status": self.status,
            "speaker_profile_id": self.speaker_profile_id,
        }


class SpeakerEmbeddingEngine:
    def __init__(self, model_path: str | Path | None = None):
        self.model_path = Path(
            model_path
            or os.getenv("HUIYI_SPEAKER_EMBEDDING_MODEL")
            or DEFAULT_MODEL_PATH
        )
        if not self.model_path.is_file():
            raise FileNotFoundError(f"声纹 Embedding 模型不存在: {self.model_path}")

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(self.model_path),
            num_threads=env_int("HUIYI_SPEAKER_NUM_THREADS", 2),
            provider=os.getenv("HUIYI_SPEAKER_PROVIDER", "cpu"),
        )
        if not config.validate():
            raise RuntimeError(f"声纹 Embedding 模型配置无效: {self.model_path}")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self._compute_lock = threading.Lock()
        self.target_rms_dbfs = env_float("HUIYI_SPEAKER_TARGET_RMS_DBFS", -22)
        self.max_gain_db = env_float("HUIYI_SPEAKER_MAX_GAIN_DB", 12)
        self.min_rms = env_float("HUIYI_SPEAKER_MIN_RMS", 0.006)

    def create_tracker(self, profiles: list[dict] | None = None) -> "MeetingSpeakerTracker":
        return MeetingSpeakerTracker(self, profiles or [])

    def extract(self, pcm_samples: list[int] | np.ndarray) -> np.ndarray | None:
        audio = np.asarray(pcm_samples, dtype=np.float32)
        if audio.size == 0:
            return None
        if np.max(np.abs(audio), initial=0) > 1.5:
            audio = audio / 32768.0

        rms = float(np.sqrt(np.mean(audio * audio)))
        if not np.isfinite(rms) or rms < self.min_rms:
            return None

        target_rms = 10 ** (self.target_rms_dbfs / 20)
        max_gain = 10 ** (self.max_gain_db / 20)
        gain = min(target_rms / rms, max_gain)
        audio = np.clip(audio * gain, -0.999, 0.999)

        with self._compute_lock:
            stream = self.extractor.create_stream()
            stream.accept_waveform(SAMPLE_RATE, audio)
            if not self.extractor.is_ready(stream):
                return None
            embedding = np.asarray(self.extractor.compute(stream), dtype=np.float32)

        norm = float(np.linalg.norm(embedding))
        if not np.isfinite(norm) or norm == 0:
            return None
        return embedding / norm


class MeetingSpeakerTracker:
    def __init__(self, engine: SpeakerEmbeddingEngine, profiles: list[dict]):
        self.engine = engine
        self.window_samples = round(
            env_float("HUIYI_SPEAKER_WINDOW_SECONDS", 1.5) * SAMPLE_RATE
        )
        self.step_samples = round(
            env_float("HUIYI_SPEAKER_WINDOW_STEP_SECONDS", 0.75) * SAMPLE_RATE
        )
        self.similarity_threshold = env_float(
            "HUIYI_SPEAKER_SIMILARITY_THRESHOLD", 0.55
        )
        self.identity_threshold = env_float(
            "HUIYI_SPEAKER_IDENTITY_THRESHOLD", 0.68
        )
        self.clusters: dict[str, SpeakerCluster] = {}
        self.profiles = [
            {
                "id": profile["id"],
                "name": profile["name"],
                "centroid": np.asarray(profile["centroid"], dtype=np.float32),
            }
            for profile in profiles
            if profile.get("centroid")
        ]

    def _identity_for_embedding(self, embedding: np.ndarray) -> tuple[str, str, float]:
        best_profile_id = ""
        best_name = ""
        best_score = -1.0
        for profile in self.profiles:
            score = cosine_similarity(embedding, profile["centroid"])
            if score > best_score:
                best_score = score
                if score >= self.identity_threshold:
                    best_profile_id = profile["id"]
                    best_name = profile["name"]
        return best_profile_id, best_name, best_score

    def identify_preview(self, pcm_samples: list[int]) -> SpeakerAnalysis:
        embedding = self.engine.extract(pcm_samples)
        if embedding is None:
            return SpeakerAnalysis("", "正在识别发言人", 0.0, "insufficient_audio")
        profile_id, name, score = self._identity_for_embedding(embedding)
        if not profile_id:
            return SpeakerAnalysis("", "正在识别发言人", max(0.0, score), "unknown")
        return SpeakerAnalysis(
            speaker_id="",
            speaker=name,
            confidence=score,
            status="recognized",
            speaker_profile_id=profile_id,
        )

    def describe_cluster(
        self, speaker_id: str, confidence: float, status: str = "identified"
    ) -> SpeakerAnalysis:
        number = int(speaker_id.rsplit("_", 1)[-1])
        speaker_name = f"发言人 {number}"
        speaker_profile_id = ""
        profile_id, name, _ = self._identity_for_embedding(
            self.clusters[speaker_id].centroid
        )
        if profile_id:
            speaker_name = name
            speaker_profile_id = profile_id
            status = "recognized"
        return SpeakerAnalysis(
            speaker_id=speaker_id,
            speaker=speaker_name,
            confidence=confidence,
            status=status,
            speaker_profile_id=speaker_profile_id,
        )

    def _assign(self, embedding: np.ndarray) -> tuple[str, float]:
        best_id = ""
        best_score = -1.0
        for speaker_id, cluster in self.clusters.items():
            score = cosine_similarity(cluster.centroid, embedding)
            if score > best_score:
                best_id = speaker_id
                best_score = score

        if not best_id or best_score < self.similarity_threshold:
            speaker_id = f"speaker_{len(self.clusters) + 1:02d}"
            self.clusters[speaker_id] = SpeakerCluster(embedding.copy())
            return speaker_id, 1.0

        self.clusters[best_id].update(embedding)
        return best_id, best_score

    def analyze(self, pcm_samples: list[int], segment_start_ms: int) -> SpeakerAnalysis:
        samples = np.asarray(pcm_samples, dtype=np.int16)
        if len(samples) < self.window_samples:
            return SpeakerAnalysis("", "发言人", 0.0, "insufficient_audio")

        windows: list[SpeakerWindow] = []
        for start in range(0, len(samples) - self.window_samples + 1, self.step_samples):
            end = start + self.window_samples
            embedding = self.engine.extract(samples[start:end])
            if embedding is None:
                continue
            speaker_id, similarity = self._assign(embedding)
            windows.append(
                SpeakerWindow(
                    start_ms=segment_start_ms + round(start / SAMPLE_RATE * 1000),
                    end_ms=segment_start_ms + round(end / SAMPLE_RATE * 1000),
                    speaker_id=speaker_id,
                    similarity=similarity,
                )
            )

        if not windows:
            return SpeakerAnalysis("", "发言人", 0.0, "insufficient_audio")

        counts: dict[str, int] = {}
        scores: dict[str, list[float]] = {}
        for window in windows:
            counts[window.speaker_id] = counts.get(window.speaker_id, 0) + 1
            scores.setdefault(window.speaker_id, []).append(window.similarity)
        dominant_id = max(counts, key=counts.get)
        dominance = counts[dominant_id] / len(windows)
        confidence = float(np.mean(scores[dominant_id])) * dominance

        status = "identified"
        if confidence < 0.6:
            status = "uncertain"
        return self.describe_cluster(dominant_id, confidence, status)
