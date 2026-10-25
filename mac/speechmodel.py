"""Which speech model the recorder and dictation use: Settings > Recording, Small,
Standard or Large, and "I speak English only".

Measured on an M4 Pro, 48 GB: on a speaker across a
room with people talking nearby, today's multilingual model gets 11.9% of the words
wrong. Its English sibling at the same size gets 8.7%. The small one needs about 1 GB,
for an 8 GB Mac, and punctuates nearly as well.

**Punctuation decides what may be a default.** parakeet-tdt-1.1b (Large) writes one
lower-case run with no stops, and the whole product leans on punctuation: voice mode
waits for a full stop to know a turn is over (turn.py), so every turn took 2.0 s longer
on the user's Mac, and the early ask never fired; dictation pastes without capitals;
transcripts read as one breath. It was this Mac's default from 2026-10-09 to 10-11
(measurements were on normalised text, which hid it). So only a model that
punctuates is ever a default, and Large is not offered until something restores
punctuation to it.

    key = chosen(cfg)            "small", "standard_en", "standard" or "large"
    path = local(key)            its folder in the Hugging Face cache, or None
    fetch(key, tell)             downloads it, pinned, telling progress
    name = for_load(cfg, log)    what to load now: the chosen one if it's there, else
                                 the best one that is (the chosen one comes next time)
    migrate(cfg)                 a stored size that is no longer offered, moved

Every model is pinned to the revision that was measured, and comes from Hugging Face the
first time it's used. Nothing else is downloaded. Config: "speech_model" ("small" or
"standard"; unset means the default for this Mac) and "english_only" (unset means from
the Mac's own language). A full model id in "speech_model" is loaded as it is, whatever
it does: that is the way to another model, Large among them.
"""
import os
import subprocess
import threading
from collections import namedtuple
from pathlib import Path

# Each model as measured on this Mac. punctuates: it writes capitals and stops, checked
# by a real decode (tests/test_speech_model.py, and tools/punctuation_check.py for a new one).
Model = namedtuple("Model", "repo revision bytes words punctuates")
MODELS = {
    "small": Model("mlx-community/parakeet-tdt_ctc-110m", "d62547387c356a1ab6bb3d85d98b2103f655282e",
                   458690629, "Small, English", True),
    "standard_en": Model("mlx-community/parakeet-tdt-0.6b-v2", "8ae155301e23d820d82aa60d24817c900e69e487",
                         2471596080, "Standard, English", True),
    "standard": Model("mlx-community/parakeet-tdt-0.6b-v3", "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15",
                      2508532829, "Standard, 25 languages", True),
    # Fewest words wrong (7.5% against 8.7%), and no punctuation at all: not a default,
    # not offered in Settings (its id by hand still loads it).
    "large": Model("mlx-community/parakeet-tdt-1.1b", "a48da3b2e1aa436c4077acb978bb3a2b65fd1b45",
                   4282297109, "Large, English", False),
}
FILES = ("config.json", "model.safetensors")
SIZES = ("small", "standard", "large")   # a size config may hold; OFFERED is what Settings shows
OFFERED = ("small", "standard")
OLD_DEFAULT = MODELS["standard"].repo    # what `standby setup` used to write as "model": not a choice
SMALL_UNDER = 9.0                        # GB of memory: at or under it, Small by default


def memory_gb():
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip()) / 1e9
    except (OSError, ValueError, subprocess.SubprocessError):
        return 16.0


def memory_label(mem=None):
    """This Mac's memory as Apple sells it (8, 16, ...), for Settings' words."""
    mem = memory_gb() if mem is None else mem
    return int(round(mem * 1e9 / 2 ** 30))


def mac_speaks_english():
    """The Mac's first language is English (its own setting, not a guess from speech)."""
    try:
        out = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                             timeout=5).stdout
        first = next((w.strip(' "(),') for w in out.split() if w.strip(' "(),')), "")
        return first.lower().startswith("en")
    except (OSError, subprocess.SubprocessError):
        return (os.environ.get("LANG") or "").lower().startswith("en")


def default_size(mem=None):
    """Standard on any Mac with the memory for it: it punctuates, and the one model that
    beats it does not (see the module). Small where Standard's 2.6 GB is too much."""
    mem = memory_gb() if mem is None else mem
    return "small" if mem <= SMALL_UNDER else "standard"


def choice(cfg, mem=None, english=None):
    """(size, english_only) as Settings shows them: the user's, else this Mac's default."""
    size = cfg.get("speech_model")
    if size not in SIZES:
        size = default_size(mem)
    english_only = cfg.get("english_only")
    if english_only is None:
        english_only = mac_speaks_english() if english is None else english
    return size, bool(english_only)


def migrate(cfg):
    """A stored size that is no longer offered: moved to this Mac's default, and what
    changed, for the app to log and say (or None).

    Settings wrote the size shown even when the user never touched the row, so a stored
    "large" is as likely the old default as a choice: it cannot be told apart, and Large
    breaks punctuation, so it moves. Anyone who wants it says so by id."""
    size = cfg.get("speech_model")
    if size not in SIZES or size in OFFERED:
        return None
    cfg["speech_model"] = default_size()
    return (size, cfg["speech_model"])


def chosen(cfg, mem=None, english=None):
    """The model the choice means. Small and Large are English only: someone who speaks
    other languages gets Standard's multilingual one, whatever the size."""
    size, english_only = choice(cfg, mem, english)
    if not english_only:
        return "standard"
    return "standard_en" if size == "standard" else size


def _hub():
    root = os.environ.get("HF_HUB_CACHE")
    if root:
        return Path(root)
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def _snapshot(key):
    repo, rev = MODELS[key].repo, MODELS[key].revision
    return _hub() / ("models--" + repo.replace("/", "--")) / "snapshots" / rev


def local(key):
    """Its folder, if every file it needs is there."""
    snap = _snapshot(key)
    return snap if all((snap / f).is_file() for f in FILES) else None


def _blob_bytes(key):
    blobs = _hub() / ("models--" + MODELS[key].repo.replace("/", "--")) / "blobs"
    try:
        return sum(p.stat().st_size for p in blobs.iterdir())
    except OSError:
        return 0


def fetch(key, tell=lambda st: None):
    """Downloads `key` (its pinned revision) if it isn't all there, telling
    {"state": "downloading", "done", "total"} every half second; its folder."""
    if local(key) is not None:
        return local(key)
    from huggingface_hub import hf_hub_download
    repo, rev, total = MODELS[key].repo, MODELS[key].revision, MODELS[key].bytes
    base, stop = _blob_bytes(key), threading.Event()

    def watch():
        while not stop.wait(0.5):
            tell({"state": "downloading", "done": min(total - 1, _blob_bytes(key) - base), "total": total})
    tell({"state": "downloading", "done": 0, "total": total})
    threading.Thread(target=watch, name="speech-model-download", daemon=True).start()
    try:
        for name in FILES:
            hf_hub_download(repo, name, revision=rev)
    finally:
        stop.set()
    tell({"state": "ready", "done": total, "total": total})
    return local(key)


def for_load(cfg, log=lambda m: None, mem=None, english=None):
    """What to load now: a folder (pinned) or, for one set by hand, its repo id.

    A full id in "speech_model" or "model" (other than the old default `standby setup`
    wrote) is the user's own, loaded as it is. Otherwise the chosen one, if it's here;
    else the best one that is, for now: the app downloads the chosen one meanwhile (or the
    installer did), and the next session has it. With none here at all, the chosen one
    is downloaded first."""
    own = cfg.get("speech_model") if "/" in str(cfg.get("speech_model") or "") else cfg.get("model")
    if own and own != OLD_DEFAULT:
        return own
    key = chosen(cfg, mem, english)
    if not MODELS[key].punctuates:            # a size stored before migrate ran, or by hand
        log("speech: %s writes no capitals or full stops: voice turns end slower, and "
            "dictation pastes without them" % MODELS[key].words)
    if local(key) is not None:
        return str(local(key))
    # The best one that is here: punctuated before not, bigger before smaller.
    for other in ("standard_en", "standard", "small", "large"):
        if other != key and local(other) is not None:
            log("speech: %s isn't downloaded yet: %s for now" % (MODELS[key].words, MODELS[other].words))
            return str(local(other))
    if os.environ.get("STANDBY_NO_DEVICES"):
        return MODELS[key].repo               # a test: never a download (its loader is a fake)
    log("speech: downloading %s (%.1f GB, once)" % (MODELS[key].words, MODELS[key].bytes / 1e9))
    return str(fetch(key))


# ---------------------------------------------------------------- loading

DEFAULT_MODEL = MODELS["standard"].repo
SMALL_MODEL = MODELS["small"].repo        # ~5x smaller, English only: `standby run --small`
SAMPLE_RATE = 16000

# MLX keeps the GPU memory it frees for reuse, with no bound unless one is set. Held to
# this, the speech model's peak is 0.3-0.4 GB lower and decodes no slower: Small from
# 1.36 to 1.09 GB, Standard from 3.19 to 2.78 GB, as tts.py.
SPEECH_CACHE = 128 * 1024 * 1024


def hf_cached(name):
    """True if the weights for `name` are already in the local HF cache."""
    root = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    snap = root / ("models--" + name.replace("/", "--")) / "snapshots"
    return snap.is_dir() and any(snap.iterdir())


def speech_name(name):
    """A model's repo id, from its pinned folder or as it is."""
    for m in MODELS.values():
        if ("models--" + m.repo.replace("/", "--")) in str(name):
            return m.repo
    return str(name)


def hold_cache(log=lambda m: None, limit=None):
    from native import speech
    speech.hold_cache(SPEECH_CACHE if limit is None else limit, log)


def load_model(name, log=lambda m: None, limit=None):
    """Load the speech model, logging where the time goes: the recorder's, and
    dictation's own copy.

    Once weights are cached, HF Hub is switched to offline mode. Otherwise every
    start makes network round-trips to check for newer weights, which on a
    slow network was the likely source of a ~1 minute start (the same model
    loaded in 1.6 s in the probe).
    """
    import time
    t0 = time.time()
    if os.path.isdir(name):                  # a pinned model's own folder (for_load)
        os.environ["HF_HUB_OFFLINE"] = "1"
        log("load: %s, from its folder, HF Hub offline" % speech_name(name))
    elif hf_cached(name):
        os.environ["HF_HUB_OFFLINE"] = "1"
        log("load: %s is cached, HF Hub offline mode ON (no network check)" % name)
    else:
        log("load: %s NOT cached, downloading (one time)..." % name)
    from native import speech
    speech.prepare()
    hold_cache(log, limit)
    t1 = time.time()
    log("load: import parakeet_mlx took %.1fs" % (t1 - t0))
    try:
        model = speech.open(name)
    except Exception as exc:
        if name == SMALL_MODEL:
            log("load: small model failed (%r), falling back to %s" % (exc, DEFAULT_MODEL))
            return load_model(DEFAULT_MODEL, log, limit)
        raise
    t2 = time.time()
    log("load: from_pretrained took %.1fs" % (t2 - t1))
    # Warm-up decode so the first real sentence is not slowed by MLX compilation.
    try:
        speech.warm(model, SAMPLE_RATE)
    except Exception as exc:
        log("load: warm-up skipped (%r)" % exc)
    t3 = time.time()
    log("load: warm-up decode took %.1fs | model ready in %.1fs total" % (t3 - t2, t3 - t0))
    return model
