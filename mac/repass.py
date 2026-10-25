#!/usr/bin/env python3
"""Archival re-pass. Re-transcribes a finished session's saved audio with
whisper-large-v3-turbo, which is slower than the live model but more accurate on
proper nouns, numbers and acronyms - exactly what the live pass gets wrong and
exactly what matters when the transcript becomes minutes.

Writes transcript.repass.jsonl next to the live transcript and delivers it to
where Claude Code runs, which prefers it to the live one.
"""

import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))
SESSIONS = ROOT / "sessions"
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"   # a fixed revision, as every model here
MODEL_FILES = ("config.json", "weights.safetensors")


def pinned(model):
    """The default model at its fixed revision, as a folder (downloaded once, about
    1.6 GB, checked by Hugging Face against its sha256); one given with --model as it is."""
    if model != DEFAULT_MODEL or os.environ.get("STANDBY_NO_DEVICES"):
        return model                          # the user's own, or a test: never a download
    from huggingface_hub import hf_hub_download
    paths = [hf_hub_download(DEFAULT_MODEL, name, revision=DEFAULT_REVISION) for name in MODEL_FILES]
    return str(Path(paths[0]).parent)


def load_wav(path):
    """Read a 16 kHz mono PCM16 wav into a float32 numpy array, no ffmpeg."""
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise SystemExit("%s is not mono PCM16" % path)
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if rate != 16000:
        raise SystemExit("%s is %d Hz, expected 16000" % (path, rate))
    return audio


def transcribe(audio, model, prompt=None):
    """Prompt: the user's own names, for every 30 s window (native.speech.whisper)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from native import speech
    return speech.whisper(audio, pinned(model), prompt)


def names_prompt():
    """The names dictation learned from the user's projects, if any."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import vocabulary
        return vocabulary.prompt(), vocabulary.echoes
    except Exception as exc:                 # the re-pass goes on without it
        print("[repass] no names for Whisper (%r)" % exc)
        return None, lambda text, p: False


def audio_files(sdir):
    """(speaker, wav, offset) for every kept channel file, including the
    room.2.wav style files a Resume adds, each placed on the session clock."""
    out = []
    for p in sorted(sdir.glob("*.wav")):
        name = p.name[:-4].split(".")[0]
        if p.stat().st_size <= 44 or not name:
            continue
        speaker = {"me": "Me", "others": "Others", "room": "Room"}.get(name, name.title())
        off = p.with_name(p.name + ".offset")
        out.append((speaker, p, float(off.read_text()) if off.exists() else 0.0))
    return out


def live_voices(sdir):
    """(speaker, start, end, spk) for live rows that carry a voice label."""
    out = []
    p = sdir / "transcript.jsonl"
    for line in (p.read_text().splitlines() if p.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("spk") is not None:
            out.append((r.get("speaker"), float(r["t"]), float(r.get("end", r["t"])), r["spk"]))
    return out


def dictated(sdir):
    """(start, end) of mic speech that was dictation to Claude, not the meeting."""
    p = sdir / "dictation.jsonl"
    out = []
    for line in (p.read_text().splitlines() if p.exists() else []):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "t" in r and "end" in r:
            out.append((float(r["t"]), float(r["end"])))
    return out


def was_dictation(speaker, start, end, windows):
    if speaker not in ("Room", "Me"):
        return False
    hit = sum(max(0.0, min(end, e) - max(start, s)) for s, e in windows)
    return hit > 0.5 * max(end - start, 0.01)


def best_voice(speaker, start, end, voices):
    """The live voice label that overlaps this segment the most, if any."""
    best, most = None, 0.0
    for sp, s, e, spk in voices:
        overlap = min(end, e) - max(start, s)
        if sp == speaker and overlap > most:
            best, most = spk, overlap
    return best


def main():
    ap = argparse.ArgumentParser(prog="standby repass")
    ap.add_argument("session", nargs="?",
                    help="session name (default: most recent)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-ship", action="store_true")
    args = ap.parse_args()

    if args.session:
        sdir = SESSIONS / args.session
    else:
        candidates = sorted(SESSIONS.glob("*/"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise SystemExit("no sessions in %s" % SESSIONS)
        sdir = candidates[-1]
    if not sdir.is_dir():
        raise SystemExit("no such session: %s" % sdir)
    session = sdir.name

    wavs = audio_files(sdir)
    if not wavs:
        raise SystemExit("no audio kept for %s (started with --no-audio?)" % session)

    rows = []
    voices = live_voices(sdir)
    skip = dictated(sdir)
    prompt, echoes = names_prompt()
    if prompt:
        print("[repass] with %d of your names in Whisper's prompt" % (prompt.count(",") + 1))
    for speaker, path, offset in wavs:
        print("[repass] %s: %s (%.1f MB) with %s"
              % (speaker, path.name, path.stat().st_size / 1e6, args.model))
        started = time.time()
        result = transcribe(load_wav(path), args.model, prompt)
        for seg in result.get("segments", []):
            text = (seg.get("text") or "").strip()
            if not text or echoes(text, prompt):
                continue
            row = {"t": round(float(seg["start"]) + offset, 2),
                   "end": round(float(seg["end"]) + offset, 2),
                   "speaker": speaker, "text": text}
            if was_dictation(speaker, row["t"], row["end"], skip):
                continue
            spk = best_voice(speaker, row["t"], row["end"], voices)
            if spk is not None:
                row["spk"] = spk
            rows.append(row)
        print("[repass] %s: %d segments in %.0fs"
              % (speaker, len(rows), time.time() - started))

    rows.sort(key=lambda r: r["t"])
    out = sdir / "transcript.repass.jsonl"
    with open(out, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("[repass] wrote %s (%d lines)" % (out, len(rows)))

    if args.no_ship:
        return 0
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import transport
    t = transport.for_config()
    if t.write("live/%s.repass.jsonl" % session, out.read_bytes()):
        print("[repass] delivered to %s" % t.label)
    else:
        print("[repass] delivery to %s failed; run `standby check`, then retry" % t.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
