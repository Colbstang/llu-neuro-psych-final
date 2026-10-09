# Local speech synthesis

Install the pinned model and its isolated Python runtime with:

```sh
python3 StudyApp/install_tts.py
```

The installer stores the model and runtime under `~/Library/Application Support/LLU Study/`. It verifies SHA-256 hashes for every model file and the KittenTTS 0.8.1 wheel. The Python dependencies are pinned in `tts-requirements.lock`. The app server uses `tts_service.get_tts_service()`; its `status()` method reports model availability and its `synthesize(text, voice="Bella")` method returns 24 kHz mono WAV bytes.

The model is KittenML's 40M parameter Micro 0.8 ONNX model, revision `1ccf72b2c2048fd17efac7de2fab32d10e225084`. The Hugging Face repository identifies it as Apache 2.0 licensed: [model card and license metadata](https://huggingface.co/KittenML/kitten-tts-micro-0.8). The integration loads the pinned ONNX and voice files directly from disk. It does not call the model hub during synthesis.

Supported voices are Bella, Jasper, Luna, Bruno, Rosie, Hugo, Kiki, and Leo. The service is intended for short study terms and limits each request to 180 characters. Model size and voice design do not guarantee correct pronunciation of every medical word; review speech for unfamiliar terms.
