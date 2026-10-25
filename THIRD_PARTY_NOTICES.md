# Third-party notices

Standby is MIT licensed (see [LICENSE](LICENSE)). This file lists the code it adapts from other
projects and the models it downloads, each under its own license.

## Code

### Website GitHub mark (MIT)

[docs/img/github.svg](docs/img/github.svg) is `mark-github-24.svg` from
[Primer Octicons](https://github.com/primer/octicons). Copyright (c) 2026 GitHub Inc.
The upstream MIT license is included in [docs/img/github.LICENSE](docs/img/github.LICENSE).

### The diarizer's streaming state, ported from FluidAudio (Apache-2.0)

[mac/native/macos/diarizer.py](mac/native/macos/diarizer.py), the section "the streaming state"
(`new_state`, `_scores`, `_compress`, `update`), is a port of `Nemotron3StateUpdater` from
FluidAudio:

- Upstream: [FluidInference/FluidAudio](https://github.com/FluidInference/FluidAudio),
  `Sources/FluidAudio/Diarizer/Nemotron3/Nemotron3StateUpdater.swift`, at commit
  [1b0ed50](https://github.com/FluidInference/FluidAudio/blob/1b0ed50d686873128a4129b3510ce3914c849bab/Sources/FluidAudio/Diarizer/Nemotron3/Nemotron3StateUpdater.swift).
- License: Apache License 2.0, full text in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).
  FluidAudio's repository carries no NOTICE file.
- FluidAudio's code is itself a port of `SortformerModules.streaming_update_async` (batch size 1)
  from [NVIDIA NeMo](https://github.com/NVIDIA/NeMo), also Apache License 2.0. NeMo's repository
  carries no NOTICE file (its `THIRD-PARTY-NOTICES` covers NeMo's own dependencies, none of which is
  used here).
- Modifications: rewritten from Swift (with Accelerate) in Python with NumPy; the state is a plain
  dictionary of arrays rather than a struct; the sizes are module constants for the 8-speaker
  checkpoint and the "fast" preset, in place of a configuration object. The arithmetic (the
  fixed-size speaker cache and FIFO, the learned silence embedding, the score-based cache
  compression with latest-frame boosting) follows the original.

## Models

Downloaded once by [install.sh](install.sh) or by the app, from a fixed revision, and run on your
Mac. None is in this repository.

| Model | Used for | Source, pinned | Checked | License |
|---|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v3 (MLX) | Speech to text (default) | [mlx-community/parakeet-tdt-0.6b-v3](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3) @ `ed2b7e8c` | Hugging Face sha256 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), from [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) |
| NVIDIA Parakeet TDT 0.6B v2 (MLX) | Speech to text (English) | [mlx-community/parakeet-tdt-0.6b-v2](https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v2) @ `8ae15530` | Hugging Face sha256 | CC BY 4.0, from [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) |
| NVIDIA Parakeet TDT-CTC 110M (MLX) | Speech to text (small) | [mlx-community/parakeet-tdt_ctc-110m](https://huggingface.co/mlx-community/parakeet-tdt_ctc-110m) @ `d6254738` | Hugging Face sha256 | CC BY 4.0, from [nvidia/parakeet-tdt_ctc-110m](https://huggingface.co/nvidia/parakeet-tdt_ctc-110m) |
| NVIDIA Parakeet TDT 1.1B (MLX) | Speech to text (only if chosen by hand) | [mlx-community/parakeet-tdt-1.1b](https://huggingface.co/mlx-community/parakeet-tdt-1.1b) @ `a48da3b2` | Hugging Face sha256 | CC BY 4.0, from [nvidia/parakeet-tdt-1.1b](https://huggingface.co/nvidia/parakeet-tdt-1.1b) |
| Whisper large-v3-turbo (MLX) | The archival re-pass (`standby repass`) | [mlx-community/whisper-large-v3-turbo](https://huggingface.co/mlx-community/whisper-large-v3-turbo) @ `a4aaeec0` | Hugging Face sha256 | MIT, from [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) |
| Pocket TTS (MLX) | Standby's voice | [mlx-community/pocket-tts](https://huggingface.co/mlx-community/pocket-tts) @ `cbf71d5f`, voices from [kyutai/pocket-tts-without-voice-cloning](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning) @ `d4fdd22a` | Hugging Face sha256 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), by Kyutai ([kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts)) |
| Nemotron-3-Diarization (Core ML) | Who is speaking | [FluidInference/nemotron-3-diarization-coreml](https://huggingface.co/FluidInference/nemotron-3-diarization-coreml) @ `25a90f97` | sha256 in install.sh | [OpenMDW License 1.1](https://github.com/OpenMDW/OpenMDW/blob/main/1.1/LICENSE.OpenMDW-1.1), from [nvidia/Nemotron-3-Diarization](https://huggingface.co/nvidia/nemotron-3-diarization) |
| TitaNet-small (ONNX) | Voice prints | [sherpa-onnx release "speaker-recongition-models"](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models), `nemo_en_titanet_small.onnx` | sha256 in install.sh | The NeMo Toolkit's license, Apache 2.0 ([NGC](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/titanet_small)); converted by [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache 2.0) |
| Smart Turn v3.2 (ONNX) | Has the speaker finished? | [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3) @ `f766f81d`, `smart-turn-v3.2-cpu.onnx` | sha256 in install.sh | [BSD 2-Clause](https://huggingface.co/pipecat-ai/smart-turn-v3) |
| Silero VAD v5.1.2 (ONNX) | Is talking over the voice speech? | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) tag `v5.1.2` (`6478567`), `silero_vad.onnx` | sha256 in install.sh | [MIT](https://github.com/snakers4/silero-vad/blob/master/LICENSE) |

CC BY 4.0 asks for attribution: Pocket TTS is by Kyutai, and Parakeet by NVIDIA, as above.
