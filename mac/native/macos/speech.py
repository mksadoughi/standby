"""native/speech.py on a Mac: Parakeet and Whisper on MLX, which runs on Apple Silicon's GPU."""
import contextlib
import sys


def prepare():
    import parakeet_mlx  # noqa: F401


def hold_cache(limit, log=lambda m: None):
    # MLX keeps the GPU memory it frees for reuse, with no bound unless one is set.
    try:
        import mlx.core as mx
        (getattr(mx, "set_cache_limit", None) or mx.metal.set_cache_limit)(limit)
    except Exception as exc:                 # an MLX without it: the model still loads
        log("load: MLX's cache not held (%r)" % (exc,))


def open(name):
    from parakeet_mlx import from_pretrained
    return from_pretrained(name)


def warm(model, sample_rate):
    import numpy as np
    import mlx.core as mx
    with model.transcribe_stream(context_size=(256, 256), depth=1) as t:
        t.add_audio(mx.array(np.zeros(sample_rate, dtype="float32")))
        _ = t.result


def stream_decode(model, audio):
    import mlx.core as mx
    ctx = model.transcribe_stream(context_size=(256, 256), depth=1)
    with ctx as t:
        t.add_audio(mx.array(audio))
        result = getattr(t, "result", None)
        try:
            words = [(float(k.start), float(k.end), k.text) for k in (getattr(result, "tokens", None) or [])]
        except Exception:
            words = None
        return (getattr(result, "text", "") or "").strip(), words


def decode(model, audio, chunk_seconds=120.0, overlap_seconds=15.0):
    """Unlike the recorder's pause-by-pause decode, the model sees every word in context.
    Long recordings go in overlapping chunks merged the way parakeet's own transcribe()
    does; that one wants a file and ffmpeg, this takes the samples. 5 s of speech: 0.1 s."""
    import mlx.core as mx
    from parakeet_mlx.audio import get_logmel
    cfg = model.preprocessor_config
    x = mx.array(audio.astype("float32"))        # get_logmel needs float32
    size = int(chunk_seconds * cfg.sample_rate)
    if len(audio) <= size:
        return model.generate(get_logmel(x, cfg))[0].text.strip()
    from parakeet_mlx import DecodingConfig
    from parakeet_mlx.alignment import (merge_longest_common_subsequence, merge_longest_contiguous,
                                        sentences_to_result, tokens_to_sentences)
    dc = DecodingConfig()
    step = size - int(overlap_seconds * cfg.sample_rate)
    tokens = []
    for start in range(0, len(audio), step):
        end = min(start + size, len(audio))
        if end - start < cfg.hop_length:
            break
        part = model.generate(get_logmel(x[start:end], cfg), decoding_config=dc)[0]
        offset = start / float(cfg.sample_rate)
        for sentence in part.sentences:
            for tok in sentence.tokens:
                tok.start += offset
                tok.end = tok.start + tok.duration
        if not tokens:
            tokens = part.tokens
        else:
            try:
                tokens = merge_longest_contiguous(tokens, part.tokens, overlap_duration=overlap_seconds)
            except RuntimeError:
                tokens = merge_longest_common_subsequence(tokens, part.tokens,
                                                          overlap_duration=overlap_seconds)
        if end == len(audio):
            break
    return sentences_to_result(tokens_to_sentences(tokens, dc.sentence)).text.strip()


def release():
    mx = sys.modules.get("mlx.core")      # only if a real model was in use
    if mx is not None:
        try:
            (getattr(mx, "clear_cache", None) or mx.metal.clear_cache)()
        except Exception:
            pass


def whisper(audio, model, prompt=None):
    """With condition_on_previous_text off, Whisper's initial_prompt alone reaches only
    the first window, so every_window gives it to the rest."""
    import mlx_whisper
    kw = dict(path_or_hf_repo=model, word_timestamps=False, condition_on_previous_text=False)
    if not prompt:
        return mlx_whisper.transcribe(audio, **kw)
    with every_window(prompt):
        return mlx_whisper.transcribe(audio, initial_prompt=prompt, **kw)


@contextlib.contextmanager
def every_window(prompt):
    """While it lasts, a window whose prompt Whisper reset (to nothing) gets
    `prompt` instead: its DecodingOptions, made per window in mlx_whisper's
    transcribe, wrapped. Yields False (and changes nothing) where that isn't
    there, e.g. another mlx_whisper version."""
    mod = sys.modules.get("mlx_whisper.transcribe")
    real = getattr(mod, "DecodingOptions", None)
    if real is None:
        yield False
        return

    def options(**kw):
        if not kw.get("prompt"):
            kw["prompt"] = prompt
        return real(**kw)
    mod.DecodingOptions = options
    try:
        yield True
    finally:
        mod.DecodingOptions = real
