"""Persistent local KittenTTS worker. Communicates with tts_service over JSONL."""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import wave
from pathlib import Path


MODEL_ID = "KittenML/kitten-tts-micro-0.8"
MODEL_REVISION = "1ccf72b2c2048fd17efac7de2fab32d10e225084"
SAMPLE_RATE = 24_000
VOICES = ("Bella", "Jasper", "Luna", "Bruno", "Rosie", "Hugo", "Kiki", "Leo")
MAX_TEXT_CHARS = 180


def _wav_bytes(samples) -> bytes:
    import numpy as np

    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    pcm = np.clip(data, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2", copy=False)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16.tobytes())
    return output.getvalue()


def _load_model(model_dir: Path):
    # Instantiate from explicit local paths. This avoids the package's
    # Hugging Face downloader, so inference cannot make a model network call.
    # KittenTTS 0.8 imports `misaki.en` and `misaki.espeak` in its ONNX module.
    # The latter registers the bundled eSpeak shared library and is required;
    # the former is never used. Stub the unused English G2P import so runtime
    # setup does not need misaki's unrelated spaCy/Torch dependency tree.
    import sys
    import types

    import misaki.espeak  # noqa: F401

    sys.modules.setdefault("misaki.en", types.ModuleType("misaki.en"))

    from kittentts.onnx_model import KittenTTS_1_Onnx

    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("version") != "0.8" or config.get("type") != "ONNX2":
        raise RuntimeError("The installed model is not the pinned Kitten Micro 0.8 model")
    return KittenTTS_1_Onnx(
        model_path=str(model_dir / config["model_file"]),
        voices_path=str(model_dir / config["voices"]),
        speed_priors=config.get("speed_priors", {}),
        voice_aliases=config.get("voice_aliases", {}),
    )


def run(model_dir: Path) -> int:
    try:
        model = _load_model(model_dir)
        print(json.dumps({"ready": True, "sample_rate": SAMPLE_RATE, "voices": VOICES}), flush=True)
    except Exception as exc:
        print(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            text = request.get("text")
            voice = request.get("voice", "Bella")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
                raise ValueError(f"text must contain 1-{MAX_TEXT_CHARS} characters")
            if voice not in VOICES:
                raise ValueError(f"voice must be one of: {', '.join(VOICES)}")
            started = time.perf_counter()
            samples = model.generate(text.strip(), voice=voice, clean_text=True)
            audio = _wav_bytes(samples)
            response = {
                "ok": True,
                "wav_b64": base64.b64encode(audio).decode("ascii"),
                "generation_seconds": round(time.perf_counter() - started, 4),
            }
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    model_path = Path(os.environ.get("LLU_TTS_MODEL_DIR", ""))
    if not model_path.is_dir():
        print(json.dumps({"ready": False, "error": "LLU_TTS_MODEL_DIR is missing or invalid"}), flush=True)
        raise SystemExit(1)
    raise SystemExit(run(model_path))
