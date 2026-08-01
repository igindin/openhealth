"""Optional local-only speech transcription helpers.

The OpenHealth core keeps zero runtime dependencies. ``LocalWhisperTranscriber``
imports OpenAI Whisper only when explicitly configured with an existing local
checkpoint. It never downloads a model and never sends audio over the network.
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional


class LocalTranscriptionError(RuntimeError):
    """A local voice file could not be transcribed."""


class LocalWhisperTranscriber:
    """Lazy adapter around an installed ``openai-whisper`` package."""

    def __init__(
        self,
        model_path: Path,
        language: str = "ru",
        model_loader: Optional[Callable[[str], Any]] = None,
    ):
        self.model_path = Path(model_path).expanduser()
        self.language = str(language or "ru")
        self._model_loader = model_loader
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        if not self.model_path.is_file():
            raise LocalTranscriptionError(
                "local Whisper checkpoint not found: %s" % self.model_path
            )
        loader = self._model_loader
        if loader is None:
            try:
                import whisper
            except ImportError as exc:
                raise LocalTranscriptionError(
                    "openai-whisper is not installed in this Python environment"
                ) from exc

            def loader(path):
                return whisper.load_model(path, device="cpu")

        try:
            self._model = loader(str(self.model_path))
        except Exception as exc:
            raise LocalTranscriptionError("could not load the local Whisper model") from exc
        return self._model

    def transcribe(self, audio_path: Path) -> Dict[str, Any]:
        """Transcribe one local audio file and return text plus provenance."""
        source = Path(audio_path)
        if not source.is_file():
            raise LocalTranscriptionError("voice file not found: %s" % source)
        model = self._load()
        try:
            result = model.transcribe(
                str(source),
                language=self.language,
                task="transcribe",
                fp16=False,
            )
        except Exception as exc:
            raise LocalTranscriptionError("local Whisper transcription failed") from exc
        if not isinstance(result, dict):
            raise LocalTranscriptionError("local Whisper returned an invalid result")
        text = str(result.get("text") or "").strip()
        if not text:
            raise LocalTranscriptionError("local Whisper returned an empty transcript")

        segments = [segment for segment in result.get("segments") or [] if isinstance(segment, dict)]
        logprobs = [
            float(segment["avg_logprob"])
            for segment in segments
            if isinstance(segment.get("avg_logprob"), (int, float))
        ]
        no_speech = [
            float(segment["no_speech_prob"])
            for segment in segments
            if isinstance(segment.get("no_speech_prob"), (int, float))
        ]
        metadata = {
            "backend": "openai-whisper-local",
            "model": self.model_path.name,
            "language": str(result.get("language") or self.language),
            "segments": len(segments),
        }
        if logprobs:
            metadata["avg_logprob"] = round(sum(logprobs) / len(logprobs), 4)
        if no_speech:
            metadata["max_no_speech_prob"] = round(max(no_speech), 4)
        return {"text": text, "metadata": metadata}
