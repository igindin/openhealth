import pytest

from openhealth.local_transcription import (
    LocalTranscriptionError,
    LocalWhisperTranscriber,
)


class FakeWhisperModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.result


def test_local_whisper_is_lazy_and_records_provenance(tmp_path):
    checkpoint = tmp_path / "small.pt"
    checkpoint.write_bytes(b"MODEL")
    audio = tmp_path / "voice.oga"
    audio.write_bytes(b"OPUS")
    model = FakeWhisperModel(
        {
            "text": "уточнение блюда",
            "language": "ru",
            "segments": [
                {"avg_logprob": -0.2, "no_speech_prob": 0.01},
                {"avg_logprob": -0.4, "no_speech_prob": 0.03},
            ],
        }
    )
    loaded = []

    def loader(path):
        loaded.append(path)
        return model

    transcriber = LocalWhisperTranscriber(checkpoint, model_loader=loader)
    assert loaded == []
    result = transcriber.transcribe(audio)
    assert loaded == [str(checkpoint)]
    assert result["text"] == "уточнение блюда"
    assert result["metadata"] == {
        "backend": "openai-whisper-local",
        "model": "small.pt",
        "language": "ru",
        "segments": 2,
        "avg_logprob": -0.3,
        "max_no_speech_prob": 0.03,
    }
    assert model.calls == [
        (
            str(audio),
            {"language": "ru", "task": "transcribe", "fp16": False},
        )
    ]
    transcriber.transcribe(audio)
    assert loaded == [str(checkpoint)]


def test_local_whisper_requires_existing_checkpoint(tmp_path):
    audio = tmp_path / "voice.oga"
    audio.write_bytes(b"OPUS")
    transcriber = LocalWhisperTranscriber(
        tmp_path / "missing.pt",
        model_loader=lambda path: FakeWhisperModel({"text": "unused"}),
    )
    with pytest.raises(LocalTranscriptionError, match="checkpoint not found"):
        transcriber.transcribe(audio)


def test_local_whisper_rejects_empty_transcript(tmp_path):
    checkpoint = tmp_path / "small.pt"
    checkpoint.write_bytes(b"MODEL")
    audio = tmp_path / "voice.oga"
    audio.write_bytes(b"OPUS")
    transcriber = LocalWhisperTranscriber(
        checkpoint,
        model_loader=lambda path: FakeWhisperModel({"text": "   "}),
    )
    with pytest.raises(LocalTranscriptionError, match="empty transcript"):
        transcriber.transcribe(audio)
