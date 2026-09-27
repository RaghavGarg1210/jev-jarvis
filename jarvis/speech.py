"""Opt-in local transcription. Audio is never sent to a cloud speech service."""
from __future__ import annotations

import io
import os
import threading


class Speech:
    def __init__(self):
        self.enabled = os.environ.get("JARVIS_VOICE", "0") == "1"
        self.model = None
        self.lock = threading.Lock()

    def transcribe(self, audio: bytes) -> str:
        if not self.enabled:
            raise ValueError("Enable local voice with JARVIS_VOICE=1 and install the voice extra.")
        if not audio or len(audio) > 8 * 1024 * 1024:
            raise ValueError("Record up to 30 seconds of audio (maximum 8 MB).")
        with self.lock:
            try:
                from faster_whisper import WhisperModel
                if self.model is None:
                    self.model = WhisperModel(os.environ.get("JARVIS_WHISPER_MODEL", "base.en"),
                                              device="cpu", compute_type="int8")
                segments, info = self.model.transcribe(io.BytesIO(audio), beam_size=1, vad_filter=True)
                if info.duration > 35:
                    raise ValueError("Please keep voice requests under 30 seconds.")
                text = " ".join(segment.text.strip() for segment in segments).strip()
            except ImportError as exc:
                raise ValueError("Install local speech with: pip install -e '.[voice]'") from exc
            except Exception as exc:
                raise ValueError("Could not transcribe audio. Check the local voice installation or type your request.") from exc
        if not text:
            raise ValueError("No speech detected. Try again closer to the microphone.")
        return text
