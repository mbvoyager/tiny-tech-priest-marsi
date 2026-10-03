"""Optional CPU speech engines. Imports and model loading happen only when used."""
from __future__ import annotations

import argparse
import io
import wave

from .config import ServerConfig, ROOT, load_env
from .core import ServiceError

MAX_AUDIO_BYTES = 2_000_000
MAX_AUDIO_SECONDS = 20


def validate_wav(data: bytes) -> None:
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Recording exceeds 2 MB")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if (wav.getnchannels() != 1 or wav.getsampwidth() != 2
                    or wav.getframerate() not in (16000, 22050, 44100, 48000)
                    or wav.getcomptype() != "NONE"):
                raise ValueError("Use mono 16-bit PCM WAV at 16, 22.05, 44.1 or 48 kHz")
            frames = wav.getnframes()
            if not 0 < frames <= wav.getframerate() * MAX_AUDIO_SECONDS:
                raise ValueError("Recording must be 0..20 seconds long")
            if len(wav.readframes(frames)) != frames * 2:
                raise ValueError("Recording is truncated")
    except (wave.Error, EOFError) as error:
        raise ValueError("Invalid WAV recording") from error


class Speech:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.whisper = None
        self.voice = None

    def load_whisper(self):
        if self.whisper is None:
            try:
                from faster_whisper import WhisperModel
                self.whisper = WhisperModel(
                    self.config.whisper_model, device="cpu", compute_type="int8",
                    cpu_threads=self.config.threads, num_workers=1,
                    download_root=str(ROOT / "models" / "whisper"),
                )
            except Exception as error:
                raise ServiceError("Speech recognition unavailable. Install server speech packages and pre-download Whisper.") from error
        return self.whisper

    def transcribe(self, data: bytes) -> str:
        validate_wav(data)
        try:
            segments, _ = self.load_whisper().transcribe(
                io.BytesIO(data), language=self.config.whisper_language,
                beam_size=1, vad_filter=True, condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except ServiceError:
            raise
        except Exception as error:
            raise ServiceError("Speech recognition failed. Try a shorter, clearer recording.") from error
        if not text:
            raise ServiceError("I did not catch any speech. Please try again.", 422)
        return text

    def synthesize(self, text: str) -> bytes:
        if not self.config.piper_model:
            raise ServiceError("No voice configured. Set MARSI_PIPER_MODEL to a downloaded .onnx voice.")
        try:
            if self.voice is None:
                from piper import PiperVoice
                self.voice = PiperVoice.load(str(self.config.piper_model))
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                self.voice.synthesize_wav(text, wav)
            audio = output.getvalue()
            if len(audio) > 8_000_000:
                raise ServiceError("Spoken reply was too long; the text is still available.")
            return audio
        except ServiceError:
            raise
        except Exception as error:
            raise ServiceError("Voice synthesis failed. Check Piper and its .onnx and .onnx.json files.") from error


def main():
    parser = argparse.ArgumentParser(description="Pre-load local speech models before going offline")
    parser.add_argument("--env", default=".env.server")
    args = parser.parse_args()
    load_env(args.env)
    speech = Speech(ServerConfig.from_env())
    speech.load_whisper()
    print("Whisper is cached locally.")
    if speech.config.piper_model:
        speech.synthesize("Beep boop. The tiny forge is ready.")
        print("Piper voice is ready.")


if __name__ == "__main__":
    main()
