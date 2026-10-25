"""Settings > Speech: which speech model, by this Mac's memory and the user's
choice, pinned, from Hugging Face the first time. A made-up Hugging Face cache and a
stand-in hf_hub_download: nothing is downloaded. Also the app's background download
(ui.ensure_speech_model) with a stand-in fetch."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, sys, tempfile, threading, time, types
from pathlib import Path

hub = Path(tempfile.mkdtemp(prefix="sbhf-"))
os.environ["HF_HUB_CACHE"] = str(hub)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import speechmodel as sm  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


def put(key, files=sm.FILES):
    snap = hub / ("models--" + sm.MODELS[key].repo.replace("/", "--")) / "snapshots" / sm.MODELS[key].revision
    snap.mkdir(parents=True, exist_ok=True)
    for f in files:
        (snap / f).write_text("x")
    return snap


# ---------------------------------------------------------------- the choice
check("8 GB Mac: Small by default", sm.default_size(8.0) == "small" and sm.default_size(sm.SMALL_UNDER) == "small")
check("every other Mac: Standard, however much memory it has", all(
    sm.default_size(m) == "standard" for m in (10.0, 16.0, 24.0, 32.0, 48.0, 128.0)),
      [sm.default_size(m) for m in (10.0, 16.0, 32.0, 128.0)])
check("this Mac (48 GB, English): Standard, English", sm.chosen({}, 48.0, True) == "standard_en"
      and sm.MODELS["standard_en"].repo.endswith("0.6b-v2"), sm.chosen({}, 48.0, True))
check("not English only: Standard's multilingual v3, whatever the size",
      all(sm.chosen({"speech_model": z, "english_only": False}, 48.0, True) == "standard" for z in sm.SIZES)
      and sm.MODELS["standard"][0].endswith("0.6b-v3"))
check("the user's choice over the default", sm.chosen({"speech_model": "small"}, 48.0, True) == "small")
check("  an unknown one: the default", sm.choice({"speech_model": "huge"}, 16.0, True) == ("standard", True))
check("english_only unset: from the Mac's language", sm.choice({}, 16.0, False) == ("standard", False)
      and sm.choice({"english_only": True}, 16.0, False) == ("standard", True))
check("the Mac's memory and language are read", sm.memory_gb() > 1.0 and sm.mac_speaks_english() in (True, False))
check("every model pinned to a full revision, with its size", all(
    len(m.revision) == 40 and int(m.revision, 16) >= 0 and m.bytes > 1e8
    and m.repo.startswith("mlx-community/parakeet-") for m in sm.MODELS.values()))

# Punctuation (2026-10-11): parakeet-tdt-1.1b writes one lower-case run with no stops, and
# it was this Mac's default for two days. Voice mode waits for a full stop to know a turn
# is over, so every turn took 2.0 s longer (measured on the user's own log) and the early
# ask never fired; dictation pasted without capitals. So no model that fails to punctuate
# can be a default or an offer, and the flag that says so is measured, not taken on trust
# (tools/punctuation_check.py; the real decode at the end of this file).
offered = {sm.chosen({"speech_model": z, "english_only": en}) for z in sm.OFFERED for en in (True, False)}
check("every model Settings offers punctuates: nothing else may be a default",
      offered and all(sm.MODELS[k].punctuates for k in offered), sorted(offered))
check("  on every Mac, in any language: each default punctuates", all(
    sm.MODELS[sm.chosen({}, mem, en)].punctuates for mem in (8.0, 16.0, 48.0, 128.0) for en in (True, False)))
check("  Large is not among them (it does not punctuate)",
      "large" not in sm.OFFERED and not sm.MODELS["large"].punctuates and "large" in sm.SIZES)
check("a size that is no longer offered is moved to the default, and says what changed",
      sm.migrate({"speech_model": "large"}) == ("large", sm.default_size()))
check("  one that is offered, or none at all, is left alone",
      sm.migrate({"speech_model": "small"}) is None and sm.migrate({}) is None
      and sm.migrate({"speech_model": "mlx-community/parakeet-tdt-1.1b"}) is None)
moved = {"speech_model": "large"}
sm.migrate(moved)
check("  and the moved one is stored, so it is moved once", moved["speech_model"] == sm.default_size()
      and sm.migrate(moved) is None, moved)

# ---------------------------------------------------------------- what is here
check("nothing downloaded: none local", all(sm.local(k) is None for k in sm.MODELS))
put("large", files=("config.json",))
check("  a download cut short (no weights) is not local", sm.local("large") is None)
put("small")
check("all its files: local, its pinned folder", sm.local("small") == put("small")
      and sm.local("small").name == sm.MODELS["small"].revision)

# ---------------------------------------------------------------- what to load
said = []
log = said.append
cfg = {"speech_model": "small", "english_only": True}
check("the chosen one, when it's here: its folder", sm.for_load(cfg, log) == str(sm.local("small")))
check("a full id set by hand: as it is", sm.for_load({"speech_model": "someone/their-model"}, log) == "someone/their-model")
said[:] = []
put("large")
got = sm.for_load({"speech_model": "large", "english_only": True}, log)       # a size stored before migrate ran
check("one that writes no punctuation, asked for by name: loaded, and what it costs is said",
      got == str(sm.local("large")) and any("no capitals or full stops" in m and "voice turns end slower" in m
                                            for m in said), (got, said))
import shutil as _sh  # noqa: E402
_sh.rmtree(str(sm.local("large")))
said[:] = []
check("  in \"model\" too (the old way)", sm.for_load({"model": "someone/their-model"}, log) == "someone/their-model")
check("the old default `standby setup` wrote in \"model\" is no choice",
      sm.for_load({"model": sm.OLD_DEFAULT, "speech_model": "small", "english_only": True}, log) == str(sm.local("small")))
said[:] = []
got = sm.for_load({"speech_model": "large", "english_only": True}, log)
check("the chosen one not here yet: the best one that is, for now, and says so",
      got == str(sm.local("small")) and any("Large, English isn't downloaded yet: Small, English for now" in m for m in said),
      (got, said))
put("standard")
check("  the best: Standard before Small", sm.for_load({"speech_model": "large", "english_only": True}, log)
      == str(sm.local("standard")))
put("large")
_sh.rmtree(str(sm.local("standard")))
_sh.rmtree(str(sm.local("standard_en"))) if sm.local("standard_en") else None
check("  and one that punctuates before one that does not, whatever its size (Small over Large)",
      sm.for_load({"speech_model": "standard", "english_only": True}, log) == str(sm.local("small")),
      sm.for_load({"speech_model": "standard", "english_only": True}, log))
_sh.rmtree(str(sm.local("large")))
put("standard")

calls = []


def fake_download(repo_id, filename, revision=None, **kw):
    calls.append((repo_id, filename, revision))
    blobs = hub / ("models--" + repo_id.replace("/", "--")) / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    (blobs / filename).write_bytes(b"y" * 1000)
    snap = hub / ("models--" + repo_id.replace("/", "--")) / "snapshots" / revision
    snap.mkdir(parents=True, exist_ok=True)
    (snap / filename).write_text("x")
    time.sleep(0.6)                                  # long enough for a progress line
    return str(snap / filename)


sys.modules["huggingface_hub"] = types.SimpleNamespace(hf_hub_download=fake_download)
other = Path(tempfile.mkdtemp(prefix="sbhf-empty-"))
os.environ["HF_HUB_CACHE"] = str(other)
check("none here, in a test: the chosen one's id, never a download",
      sm.for_load({"speech_model": "large", "english_only": True}, log) == sm.MODELS["large"][0] and not calls)
os.environ["HF_HUB_CACHE"] = str(hub)
told = []
path = sm.fetch("standard_en", told.append)
check("fetch: from Hugging Face, the pinned revision, each file",
      calls == [(sm.MODELS["standard_en"][0], f, sm.MODELS["standard_en"][1]) for f in sm.FILES], calls)
check("  its folder, now local", path == sm.local("standard_en") and path is not None)
check("  telling its progress, then ready", told and told[0]["state"] == "downloading"
      and told[-1] == {"state": "ready", "done": sm.MODELS["standard_en"][2], "total": sm.MODELS["standard_en"][2]}
      and all(t["done"] <= t["total"] for t in told), told)
calls[:] = []
check("  and not again once here", sm.fetch("standard_en") == path and not calls)

# ---------------------------------------------------------------- the app's download
import ui  # noqa: E402
fetched, gate = [], threading.Event()


def fake_fetch(key, tell):
    fetched.append(key)
    tell({"state": "downloading", "done": 5, "total": 10})
    gate.wait(10)
    if key == "small":
        raise RuntimeError("no network")
    put(key)
    tell({"state": "ready", "done": 10, "total": 10})


ui.speechmodel = sm
sm.fetch = fake_fetch
ui.transport.save_config({"speech_model": "large", "english_only": True})
check("in a test the app never downloads", ui.ensure_speech_model() is False and not fetched)
saved = os.environ.pop("STANDBY_NO_DEVICES")         # only around these calls: the fetch is a stand-in
try:
    started = ui.ensure_speech_model()
    time.sleep(0.2)
    again = ui.ensure_speech_model()
finally:
    os.environ["STANDBY_NO_DEVICES"] = saved
st = ui.speech_status()
check("Settings learns this Mac's memory as Apple sells it, and whether the size is the user's",
      sm.memory_label(8 * 2 ** 30 / 1e9) == 8 and sm.memory_label(16 * 2 ** 30 / 1e9) == 16
      and isinstance(st["memory"], int) and st["picked"] is True
      and ui.speech_status({})["picked"] is False and ui.speech_status({"speech_model": "small"})["picked"] is True,
      (st.get("memory"), st.get("picked")))
check("the chosen one missing: downloaded in the background, one at a time",
      started is True and again is False and fetched == ["large"], (started, again, fetched))
check("  Settings sees its progress", st["model"] == "Large, English" and not st["ready"]
      and st["download"] and st["download"]["state"] == "downloading" and st["download"]["done"] == 5, st)
gate.set()
deadline = time.time() + 5
while ui.speech_status()["download"]["state"] != "ready" and time.time() < deadline:
    time.sleep(0.05)
st = ui.speech_status()
check("  then ready", st["ready"] and st["download"]["state"] == "ready", st)
saved = os.environ.pop("STANDBY_NO_DEVICES")
try:
    check("  and nothing more once it's here", ui.ensure_speech_model() is False and fetched == ["large"])
    ui.transport.save_config({"speech_model": "small", "english_only": True})
    shutil_small = sm.local("small")
    import shutil  # noqa: E402
    shutil.rmtree(str(shutil_small))
    ui.ensure_speech_model()
finally:
    os.environ["STANDBY_NO_DEVICES"] = saved
deadline = time.time() + 5
while ui.speech_status()["download"]["state"] == "downloading" and time.time() < deadline:
    time.sleep(0.05)
st = ui.speech_status()
check("a failed download: said, and the row says why", st["download"]["state"] == "error"
      and "no network" in st["download"]["error"] and not st["ready"], st)
# The app at start: a stored size that is no longer offered, moved once, and said.
ui.transport.save_config({"speech_model": "large", "english_only": True})
said_moved = ui.speech_migrate()
expected_model = sm.MODELS[sm.chosen(ui.transport.load_config())].words
check("the app moves a stored Large at start, and says why in plain words",
      said_moved and "no capitals or full stops" in said_moved and expected_model + " now." in said_moved, said_moved)
check("  the config is written, so it is moved once",
      ui.transport.load_config()["speech_model"] == sm.default_size() and ui.speech_migrate() is None,
      ui.transport.load_config().get("speech_model"))
check("  and Settings says it, with what it is now",
      ui.speech_status()["moved"] == said_moved and ui.speech_status()["model"] == expected_model,
      ui.speech_status())
ui.SPEECH_MOVED[0] = None
ui.transport.save_config({"speech_model": "small", "english_only": True})
check("  nothing to move: nothing said", ui.speech_migrate() is None and ui.speech_status()["moved"] is None)

# ---------------------------------------------------------------- the flag, against the model
# The `punctuates` flag decides what may be a default, so it is measured, not trusted:
# each model already in the user's own cache decodes one `say` sentence and must agree
# with its flag. Nothing is downloaded here and nothing is played (`say -o` writes a
# file); a model that isn't cached is left to tools/punctuation_check.py.
import shutil  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
os.environ.pop("HF_HUB_CACHE", None)              # the user's own cache: read, never written
real = [k for k in sm.MODELS if sm.local(k) is not None]
if not (real and shutil.which("say") and shutil.which("afconvert")):
    print("  (the real-decode part skipped: needs say, afconvert and a model already cached)")
else:
    import punctuation_check as pc
    os.environ["HF_HUB_OFFLINE"] = "1"
    audio = pc.speech()
    for key in real:
        text = pc.decode(sm.local(key), audio)
        print("     %s heard: %r" % (key, text))
        check("%s punctuates: %s, as its flag says" % (key, sm.MODELS[key].punctuates),
              pc.punctuates(text) == sm.MODELS[key].punctuates, text)
    check("  and \"did it punctuate\" is not fooled by a lower-case run",
          pc.punctuates("Please open the repository. Did it pass?")
          and not pc.punctuates("please open the repository did it pass")
          and not pc.punctuates("Please open the repository did it pass"), "")

# ---------------------------------------------------------------- MLX's cache, held where speech loads
# A stand-in MLX and parakeet, so nothing is decoded and no GPU is used.
import standby  # noqa: E402
import dictation  # noqa: E402
saved = {k: sys.modules.get(k) for k in ("mlx", "mlx.core", "parakeet_mlx")}
offline = os.environ.get("HF_HUB_OFFLINE")
order = []


class _Stream(object):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_audio(self, x):
        order.append("warm-up")

    result = types.SimpleNamespace(text="")


def fake_mlx(new=True, fails=False):
    def limit(n):
        if fails:
            raise RuntimeError("no Metal")
        order.append(("cache", n))
    core = types.ModuleType("mlx.core")
    core.array = lambda x: x
    if new:
        core.set_cache_limit = limit
    core.metal = types.SimpleNamespace(set_cache_limit=limit)
    top = types.ModuleType("mlx")
    top.core = core
    sys.modules["mlx"], sys.modules["mlx.core"] = top, core
    pm = types.ModuleType("parakeet_mlx")
    pm.from_pretrained = lambda name: (order.append("load"), types.SimpleNamespace(
        transcribe_stream=lambda **kw: _Stream()))[1]
    sys.modules["parakeet_mlx"] = pm


folder = tempfile.mkdtemp(prefix="sbmodel-")
try:
    fake_mlx()
    order.clear()
    standby.load_model(folder)
    check("the recorder's speech model: MLX's cache held to 128 MB, before the model loads",
          order[:2] == [("cache", 128 * 1024 * 1024), "load"] and standby.SPEECH_CACHE == 128 << 20, order)
    order.clear()
    dictation.load_model(folder)
    check("  dictation's own copy: the same", order[:2] == [("cache", 128 << 20), "load"], order)
    fake_mlx(new=False)
    order.clear()
    standby.load_model(folder)
    check("  an older MLX (mx.metal's): held the same", order[:2] == [("cache", 128 << 20), "load"], order)
    fake_mlx(fails=True)
    order.clear()
    standby.load_model(folder)
    check("  an MLX that refuses: the model still loads and warms up", order == ["load", "warm-up"], order)
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    if offline is None:
        os.environ.pop("HF_HUB_OFFLINE", None)
    else:
        os.environ["HF_HUB_OFFLINE"] = offline

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
