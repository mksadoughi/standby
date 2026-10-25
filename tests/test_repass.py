"""Archival re-pass on an app session: mic mode (room.wav), a Resume segment
(room.2.wav placed by its .offset), live voice labels carried over, and delivery
through the configured transport. Whisper is faked. The names dictation
learned go in Whisper's prompt, for every window (checked on the real
mlx_whisper's code, without a model), and a segment that only echoes them is
dropped."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, wave
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="sbrp-"))
stubs = home / "stubs"; stubs.mkdir()
(stubs / "mlx_whisper.py").write_text('''
import json, os
def transcribe(audio, **kw):
    secs = len(audio) / 16000.0
    with open(os.environ["SB_PROMPTS"], "a") as fh:
        fh.write(json.dumps(kw.get("initial_prompt")) + "\\n")
    echo = [{"start": 5.0, "end": 6.0, "text": " " + kw["initial_prompt"]}] if kw.get("initial_prompt") else []
    return {"segments": [{"start": 1.0, "end": 3.0, "text": " first words"}] + echo +
                        [{"start": secs - 2.0, "end": secs - 0.5, "text": " last words"}]}
''')
root = home / ".standby"; sdir = root / "sessions" / "meeting-1"; sdir.mkdir(parents=True)
(root / "config.json").write_text(json.dumps({"agent": {"mode": "local"}}))


def wav(path, secs):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * secs))


wav(sdir / "room.wav", 10)
wav(sdir / "room.2.wav", 8)
(sdir / "room.2.wav.offset").write_text("30.00\n")
live = [{"t": 1.2, "end": 2.8, "speaker": "Room", "spk": 1, "text": "frist words"},
        {"t": 8.1, "end": 9.4, "speaker": "Room", "spk": 2, "text": "lass words"},
        {"t": 31.0, "end": 33.0, "speaker": "Room", "spk": 2, "text": "after resume"}]
(sdir / "transcript.jsonl").write_text("".join(json.dumps(r) + "\n" for r in live))
# 36.0-37.5 (the second file's last words) was dictation to Claude, not the meeting.
(sdir / "dictation.jsonl").write_text(json.dumps({"seq": 1, "t": 36.0, "end": 37.5, "text": "last words"}) + "\n"
                                      + json.dumps({"seq": 1, "done": True}) + "\n")

prompts = home / "prompts.jsonl"
env = dict(os.environ, HOME=str(home), PYTHONPATH=str(stubs), STANDBY_ROOT=str(root), SB_PROMPTS=str(prompts))
r = subprocess.run([sys.executable, "mac/repass.py", "meeting-1"], env=env,
                   capture_output=True, text=True)
fails = []


def check(label, ok, d=""):
    print("  %-52s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:600])


check("repass runs on a mic-mode session", r.returncode == 0, r.stdout + r.stderr)
rows = [json.loads(l) for l in (sdir / "transcript.repass.jsonl").read_text().splitlines()] \
    if (sdir / "transcript.repass.jsonl").exists() else []
check("both audio files transcribed, dictation left out", len(rows) == 3, rows)
ts = [r["t"] for r in rows]
check("resumed audio placed on the session clock", ts == [1.0, 8.0, 31.0], ts)
check("channel kept as Room", all(r["speaker"] == "Room" for r in rows), rows)
check("live voice labels carried over", [r.get("spk") for r in rows] == [1, 2, 2], rows)
delivered = root / "live" / "meeting-1.repass.jsonl"
check("delivered through the transport (this Mac)", delivered.exists()
      and delivered.read_text() == (sdir / "transcript.repass.jsonl").read_text(), r.stdout)
check("says where it went", "delivered to this Mac" in r.stdout, r.stdout)
check("no vocabulary yet: no prompt", prompts.read_text().split() == ["null", "null"], prompts.read_text())

# With the names dictation learned (the kept vocabulary): in Whisper's prompt.
(root / "dictation-vocabulary.json").write_text(json.dumps(
    {"built": 1, "terms": [["Claude Code", 11], ["Orion", 4], ["Nadia Castellanos", 3]]}))
prompts.unlink()
r = subprocess.run([sys.executable, "mac/repass.py", "meeting-1", "--no-ship"], env=env, capture_output=True, text=True)
got = [json.loads(l) for l in prompts.read_text().splitlines()] if prompts.exists() else []
check("  the names, strongest first, for each file", got == ["Claude Code, Orion, Nadia Castellanos."] * 2, got)
check("  says how many, not which", "with 3 of your names in Whisper's prompt" in r.stdout and "Orion" not in r.stdout, r.stdout)
rows = [json.loads(l) for l in (sdir / "transcript.repass.jsonl").read_text().splitlines()]
check("  a segment that only echoes the names (over silence): dropped",
      len(rows) == 3 and not any("Orion" in x["text"] for x in rows), rows)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import vocabulary  # noqa: E402
check("  but a line that says one name is speech", not vocabulary.echoes("Claude Code.", "Claude Code, Orion.")
      and not vocabulary.echoes("Ask Orion, then Claude Code.", "Claude Code, Orion."))
check("  the prompt stays short (Whisper reads 223 tokens at most)",
      len(vocabulary.prompt(root / "dictation-vocabulary.json", limit=20) or "") <= 22)

# Every window: the real mlx_whisper makes DecodingOptions per window and, with
# condition_on_previous_text off, resets the prompt to nothing after the first.
try:
    import inspect
    import mlx_whisper  # noqa: F401
    mod = sys.modules["mlx_whisper.transcribe"]
except Exception as exc:
    print("  (mlx_whisper not here: %r; the every-window check skipped)" % exc)
    mod = None
if mod is not None:
    src = inspect.getsource(mod.transcribe)
    check("mlx_whisper still resets the prompt per window, through DecodingOptions",
          "DecodingOptions(**kwargs" in src and "prompt_reset_since = len(all_tokens)" in src
          and 'decode_options["prompt"] = all_tokens[prompt_reset_since:]' in src)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
    from native.macos import speech as mac_speech  # noqa: E402  (the re-pass's Whisper on a Mac)
    real = mod.DecodingOptions
    with mac_speech.every_window("Claude Code, Orion.") as on:
        reset, first = mod.DecodingOptions(prompt=[]), mod.DecodingOptions(prompt=[50, 60])
    check("  every_window: a reset window gets the names; the first keeps its own",
          on is True and reset.prompt == "Claude Code, Orion." and first.prompt == [50, 60], (reset.prompt, first.prompt))
    check("  and mlx_whisper is as it was afterwards", mod.DecodingOptions is real)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
