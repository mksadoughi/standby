"""Dictation that learns the user's words (on this Mac only, and
nothing is applied without the user's click).

- The vocabulary: the user's own terms, from the folders Claude Code has worked
  in (their names, the prose of their CLAUDE.md and README, package names, git
  authors) and the fixes already in the words file. Rebuilt at most once a day.
- Suggestions: a phrase of a dictation that sounds like one of those terms but
  isn't spelled like it ("cloud code" for "Claude Code"), counted. Only the pair
  and its count are kept, never what was dictated.
- Fix Last Dictation…: the words the user changed become rules in the words
  file's learned section, which the dictation's word fixes apply like the
  user's own lines.

Plain Python, no AppKit, so it is tested anywhere; mac/dictation.py hooks it in.
"""

import difflib
import unicodedata
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import config

ROOT = config.ROOT
VOCAB = ROOT / "dictation-vocabulary.json"
SUGGESTIONS = ROOT / "dictation-suggestions.json"
REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()

REBUILD_EVERY = 24 * 3600      # s: the vocabulary is rebuilt at most this often
FIRST_BUILD_AFTER = 60.0       # s after launch, so the app starts first
MAX_TERMS = 400
MAX_PAIRS = 40                 # suggestions kept, the least seen dropped first
MAX_DISMISSED = 200
SHOW_AFTER = 2                 # heard this often, a suggestion is offered
SHOWN = 5                      # at most this many in the menu
MIN_LIKE = 0.7                 # spelling likeness (0-1) on top of sounding the same
MIN_KEY = 3                    # sounds: at least this many, so short words never match by chance
MAX_FIX_WORDS = 4              # a change longer than this is a rewrite, not a misheard word
READ_BYTES = 65536             # of each CLAUDE.md or README
TEXT_FILES = ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "README.md")
WORD_LIST = "/usr/share/dict/words"
PROMPT_CHARS = 600             # Whisper reads at most 223 tokens of a prompt; this stays well under
LEARNED = "# learned: from Fix Last Dictation, Suggested Fixes and the meetings' own Claude. Edit or delete them freely."
FIXED_CACHE = 20000            # transcript lines kept fixed, per words file (the view polls often)

# Folders macOS guards: reading one in Standby Dictation's name would put up a
# permission prompt, unless Standby itself runs from there (so it already may).
GUARDED = [HOME / "Desktop", HOME / "Documents", HOME / "Downloads",
           HOME / "Library" / "Mobile Documents", Path("/Volumes")]


def log(msg):
    line = "[vocabulary] %s %s\n" % (time.strftime("%H:%M:%S"), msg)
    # As dictation.log: launchd gives no locale, so stderr may be ASCII; terms may not be.
    buf = getattr(sys.stderr, "buffer", None)
    if buf is not None:
        buf.write(line.encode("utf-8", "replace"))
    else:
        sys.stderr.write(line)
    sys.stderr.flush()


# ---------------------------------------------------------------- how words sound

_SOUNDS = [("tch", "x"), ("sch", "sk"), ("ch", "x"), ("sh", "x"), ("ph", "f"), ("th", "t"),
           ("ck", "k"), ("gh", ""), ("wh", "w"), ("qu", "kw"), ("dg", "j"),
           ("ce", "se"), ("ci", "si"), ("cy", "sy"), ("ge", "je"), ("gi", "ji"), ("gy", "jy"),
           ("x", "ks"), ("q", "k"), ("c", "k"), ("z", "s"), ("v", "f"), ("d", "t"), ("b", "p"),
           ("g", "k"), ("j", "x")]
_FIRST = [("kn", "n"), ("gn", "n"), ("pn", "n"), ("wr", "r"), ("ps", "s"), ("x", "s")]


def sound_key(phrase):
    """How a phrase sounds, roughly, spaces ignored: its consonants, with the
    pairs speech recognition confuses (d/t, b/p, g/k, v/f, z/s) as one, and a
    vowel kept only at the start. "cloud code" and "Claude Code" are both
    "kltkt"; "or ion" and "Orion" both "arn"."""
    s = re.sub(r"[^a-z]", "", phrase.lower())
    for a, b in _FIRST:
        if s.startswith(a):
            s = b + s[len(a):]
            break
    for a, b in _SOUNDS:
        s = s.replace(a, b)
    if not s:
        return ""
    out = "a" if s[0] in "aeiouy" else ""
    for i, ch in enumerate(s):
        if ch in "aeiouy":
            continue
        if ch in "wh" and (i + 1 >= len(s) or s[i + 1] not in "aeiouy"):
            continue                         # a w or h that isn't sounded
        if not out or out[-1] != ch:
            out += ch
    return out


def spelled(phrase):
    """A phrase as spelling compares it: lower case, letters and single spaces."""
    return " ".join(re.findall(r"[a-z]+", phrase.lower()))


def alike(a, b):
    """How alike two spellings are, 0 to 1."""
    return difflib.SequenceMatcher(None, spelled(a), spelled(b), autojunk=False).ratio()


_WORDS = {"text": None}
_ORDINARY = {}


def ordinary(word):
    """An everyday word: in macOS's word list in lower case (by itself, or
    without a plural or -ed/-ing ending). Names and products ("Claude", "Rumi")
    are not; "cloud" and "code" are. Without the list every word counts as
    everyday, so only phrases are ever suggested."""
    w = word.lower()
    if w in _ORDINARY:
        return _ORDINARY[w]
    if _WORDS["text"] is None:
        try:                                 # ~2.5 MB, read once and kept as one string
            _WORDS["text"] = "\n" + Path(WORD_LIST).read_text(encoding="utf-8", errors="ignore") + "\n"
        except OSError:
            _WORDS["text"] = ""
    text = _WORDS["text"]
    stems = [w] + [w[:-len(e)] for e in ("s", "es", "ed", "d", "ing", "ly") if w.endswith(e) and len(w) - len(e) >= 3]
    found = not text or len(w) < 3 or any(("\n%s\n" % s) in text for s in stems)
    if len(_ORDINARY) < 20000:
        _ORDINARY[w] = found
    return found


# ---------------------------------------------------------------- the vocabulary

_CODE = re.compile(r"```.*?```|`[^`\n]*`|https?://\S+|<[^>\n]*>", re.S)
_PHRASE = re.compile(r"(?<![A-Za-z])([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})(?![A-Za-z])")
_WORD = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z]{3,})(?![A-Za-z0-9_])")


def prose_terms(text):
    """The terms in a CLAUDE.md or README, from its prose: capitalised phrases
    of two or three words with a name in them ("Claude Code"), and words that
    are not everyday ones ("pywebview", "Orion"). Code (backticks, fences),
    links and tags are left out: identifiers are rarely dictated."""
    text = _CODE.sub(" ", text)
    out = []
    for m in _PHRASE.finditer(text):
        words = m.group(1).split()
        if not all(ordinary(w) for w in words):
            out.append(m.group(1))
    for m in _WORD.finditer(text):
        if not ordinary(m.group(1)):
            out.append(m.group(1))
    return out


def name_terms(name):
    """A folder's or package's name: itself, and its parts with no digits that
    are not everyday words ("orion-client": itself and "orion")."""
    name = name.split("/")[-1]
    parts = [p for p in re.split(r"[-_. ]+", name) if len(p) >= 3 and not re.search(r"\d", p) and not ordinary(p)]
    return ([name] if not re.search(r"\d", name) and len(name) >= 3 else []) + [p for p in parts if p != name]


def readable(folder, repo=REPO):
    """Whether a folder can be read without a macOS permission prompt in
    Standby Dictation's name: not in a guarded place, or in the same one as
    Standby's own code (which it already reads)."""
    folder = Path(folder)
    for g in GUARDED:
        if folder == g or g in folder.parents:
            return repo == g or g in Path(repo).parents
    return True


def git_authors(folder, run=subprocess.run):
    """The names in a folder's recent git history, if it is a git checkout."""
    if not (Path(folder) / ".git").exists() or not Path("/usr/bin/git").exists():
        return []
    try:
        r = run(["git", "-C", str(folder), "log", "--format=%an", "-n", "300"], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({n.strip() for n in (r.stdout or "").splitlines() if n.strip()}) if r.returncode == 0 else []


def package_names(folder):
    """package.json's and pyproject.toml's names."""
    out = []
    try:
        name = json.loads((Path(folder) / "package.json").read_text(encoding="utf-8", errors="replace")[:READ_BYTES]).get("name")
        if isinstance(name, str):
            out.append(name)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        m = re.search(r'(?m)^name\s*=\s*"([^"]+)"', (Path(folder) / "pyproject.toml").read_text(encoding="utf-8", errors="replace")[:READ_BYTES])
        if m:
            out.append(m.group(1))
    except OSError:
        pass
    return out


def local_projects(timeout=30):
    """The folders Claude Code has worked in on this Mac, newest first: the
    agent's own list, which reads each from its newest transcript's cwd."""
    try:
        r = subprocess.run([sys.executable, str(REPO / "plugin" / "bin" / "standby-agent"), "projects"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        got = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return []
    return [p["path"] for p in got.get("projects") or [] if isinstance(p, dict) and p.get("exists") and p.get("path")]


def learned_at(lines):
    """The index of the words file's "# learned" line, or None."""
    return next((i for i, l in enumerate(lines) if l.strip().lower().startswith("# learned")), None)


def rules_in(path):
    """(heard, meant) of every rule in a words file, the user's and the learned. A
    learned one that isn't safe to type (safe_fix), say one planted before the guard
    was there, is skipped: below the "# learned" line only, so the user's own lines
    above it are as free as ever."""
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    at, skipped = learned_at(lines), 0
    for i, line in enumerate(lines):
        line = line.strip()
        if line and not line.startswith("#") and "=>" in line:
            heard, meant = (s.strip() for s in line.split("=>", 1))
            if not heard:
                continue
            if at is not None and i > at and meant and safe_fix(heard, meant):
                skipped += 1
                continue
            out.append((heard, meant))
    if skipped:
        log("%d learned fix(es) skipped: not safe to type" % skipped)
    return out


def build(folders, words, authors=git_authors, repo=REPO):
    """The vocabulary: [(term, weight)], the strongest first. The user's own
    fixes count most, then names (git authors, packages, folders), then terms
    their CLAUDE.md and README use more than once."""
    weight, shown = {}, {}

    def add(term, w):
        """One term whatever its case, written as names are: "Orion" over "orion"."""
        term = term.strip()
        if len(spelled(term).replace(" ", "")) < 4:
            return
        k = term.lower()
        weight[k] = weight.get(k, 0) + w
        if k not in shown or (term != k and shown[k] == k):
            shown[k] = term
    for _heard, meant in rules_in(words):
        add(meant, 10)
    seen = 0
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir() or not readable(folder, repo):
            continue
        seen += 1
        for t in name_terms(folder.name):
            add(t, 3)
        for name in package_names(folder):
            for t in name_terms(name):
                add(t, 3)
        for name in authors(folder):
            add(name, 3)
            for part in name.split():
                if not ordinary(part):
                    add(part, 2)
        for f in TEXT_FILES:
            try:
                with open(folder / f, "rb") as fh:
                    text = fh.read(READ_BYTES).decode("utf-8", "replace")
            except OSError:
                continue
            for t in prose_terms(text):
                add(t, 1)
    # Prose terms seen once are mostly noise; names and fixes count from the first.
    terms = sorted(((shown[k], w) for k, w in weight.items() if w >= 2), key=lambda tw: (-tw[1], tw[0]))[:MAX_TERMS]
    return terms, seen


def index(terms):
    """{sound key: [terms]} for matching."""
    out = {}
    for term, _w in terms:
        key = sound_key(term)
        if len(key) >= MIN_KEY:
            out.setdefault(key, []).append(term)
    return out


def matches(text, idx):
    """[(heard, term)]: the phrases (one to three words) of `text` that sound
    like a term but aren't spelled like it, longest first at each place. A lone
    everyday word never counts ("the cloud" is not "Claude"), and the spelling
    must be close too, so a weak likeness never does."""
    words = re.findall(r"[A-Za-z]+", text)
    out, i = [], 0
    while i < len(words):
        hit = None
        for n in (3, 2, 1):
            phrase = " ".join(words[i:i + n])
            if len(words[i:i + n]) < n:
                continue
            for term in idx.get(sound_key(phrase), ()):
                if spelled(phrase) == spelled(term):
                    continue                 # what it is: nothing to fix (case aside)
                if n == 1 and ordinary(phrase):
                    continue
                if alike(phrase, term) >= MIN_LIKE:
                    hit = (phrase, term, n)
                    break
            if hit:
                break
        if hit:
            out.append(hit[:2])
            i += hit[2]
        else:
            i += 1
    return out


# ---------------------------------------------------------------- the user's fixes

_EDGE = "\"'“”‘’()[]{}.,;:!?…"


def _bare(word):
    return word.strip(_EDGE)


def diff_fixes(before, after):
    """([(heard, meant)], [everyday words not learned]): the words the user
    changed in a dictation. Words added or removed are edits, not mishearings,
    and so is a change longer than a few words. A lone everyday word takes a
    neighbour of four letters or more along ("cloud code => Claude code", never
    "cloud => Claude" or "the cloud => the Claude"), so the rule can't change
    the word everywhere; with no such neighbour, it is not learned."""
    a, b = before.split(), after.split()
    sa, sb = [_bare(w) for w in a], [_bare(w) for w in b]
    out, alone = [], []
    near = lambda x, y: bool(x) and x == y and len(x) >= 4
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        heard, meant = [w for w in sa[i1:i2] if w], [w for w in sb[j1:j2] if w]
        if not heard or not meant or heard == meant or len(heard) > MAX_FIX_WORDS or len(meant) > MAX_FIX_WORDS:
            continue
        if len(heard) == 1 and ordinary(heard[0]):
            if i2 < len(sa) and j2 < len(sb) and near(sa[i2], sb[j2]):
                heard, meant = heard + [sa[i2]], meant + [sb[j2]]
            elif i1 > 0 and j1 > 0 and near(sa[i1 - 1], sb[j1 - 1]):
                heard, meant = [sa[i1 - 1]] + heard, [sb[j1 - 1]] + meant
            else:
                alone.append(heard[0])
                continue
        pair = (" ".join(heard), " ".join(meant))
        if pair not in out:
            out.append(pair)
    return out, alone


def add_learned(path, rules):
    """Put rules into the words file's learned section, at its end: the user's
    own lines stay as they are, a learned rule for the same words is replaced,
    and words the user's own lines already fix are left to them. Written whole
    and then swapped in, so a reader never sees half a file. The rules written."""
    path = Path(path)
    rules, refused = guard_learned(rules)
    for _h, _m, why in refused:
        log("not learned: %s" % why)           # the reason only: the words may be anyone's
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    at = learned_at(lines)
    own, learned = (lines, []) if at is None else (lines[:at], lines[at + 1:])
    heard_of = lambda l: l.split("=>", 1)[0].strip().lower() if "=>" in l and not l.strip().startswith("#") else None
    theirs = {heard_of(l) for l in own} - {None}
    new = [(h, m) for h, m in rules if h.strip() and h.strip().lower() not in theirs]
    if not new:
        return []
    replaced = {h.lower() for h, _m in new}
    keep = [l for l in learned if heard_of(l) not in replaced]
    while own and not own[-1].strip():
        own.pop()
    out = own + ["", LEARNED] + [l for l in keep if l.strip()] + ["%s => %s" % (h, m) for h, m in new]
    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return new


# ---------------------------------------------------------------- the meeting's own words
#
# The user, 2026-10-09: the meeting's own Claude, which knows the domain, lists
# what the speech recognition got wrong, at the end of a session and every so
# often in a long one, and the fixes go into the same word list dictation uses.

WORDS_QUESTION = (
    "A housekeeping question from Standby, not from the user: answer it only for Standby, and "
    "don't mention it later. This session's transcript and the user's spoken questions came from "
    "speech recognition, which mishears names, acronyms, product and project terms as ordinary "
    "words that sound alike. From what you know of this session and its domain, list the ones it "
    "got wrong, one per line, exactly:\n"
    "heard => meant\n"
    "heard as the transcript has it, meant as it should be written. Only mishearings of names, "
    "terms and acronyms; never grammar, style or what someone meant to say. When the heard word "
    "alone is an everyday word, give it with the word next to it (\"cloud code => Claude Code\", "
    "never \"cloud => Claude\"). Only ones you are sure of. If there are none, reply NONE.")
_TERM = re.compile(r"^\s*(?:[-*\u2022]\s*|\d+[.)]\s*)?[`\"']?(.+?)[`\"']?\s*=>\s*[`\"']?(.+?)[`\"']?\s*$")


def parse_terms(answer):
    """[(heard, meant)] from the meeting Claude's answer to WORDS_QUESTION: its
    "heard => meant" lines (bullets, numbers and quotes allowed), nothing else
    (its follow-up suggestions, a NONE, prose). A change of more than a few
    words is a rewrite, not a mishearing, and is left out; so is one that
    changes nothing."""
    out = []
    for line in (answer or "").split("---", 1)[0].splitlines():
        m = _TERM.match(line)
        if not m:
            continue
        heard, meant = (" ".join(x.split()) for x in m.groups())
        if (not heard or not meant or heard == meant or len(heard.split()) > MAX_FIX_WORDS
                or len(meant.split()) > MAX_FIX_WORDS or "=>" in meant):
            continue
        if (heard, meant) not in out:
            out.append((heard, meant))
    return out


def guard_terms(terms):
    """(kept, left out): a lone everyday word never becomes a rule by itself
    ("cloud => Claude" would change "the cloud" everywhere); with the word next
    to it, or a word that isn't an everyday one, it does. Nor does a fix that
    doesn't look like a spelling fix (safe_fix)."""
    kept, dropped = [], []
    for heard, meant in terms:
        words = heard.split()
        alone = len(words) == 1 and ordinary(words[0])
        (dropped if alone or safe_fix(heard, meant) else kept).append((heard, meant))
    return kept, dropped


# ---------------------------------------------------------------- what a learned fix may be (S1)
#
# Dictation types a fix's words into whatever app has the focus, Terminal included, and
# with Return to send it presses Return after. A fix nobody typed in themselves (one the
# meeting's Claude listed, a suggestion, Fix Last Dictation's diff) is learned only when
# it looks like a spelling fix: a name or term that sounds like the words heard, in
# letters, digits and a little punctuation inside words. Never a command, never shell
# or URL characters, never on the commonest words. The user's own lines in the words
# file are theirs, as free as before.

# A word: letters or digits, with an apostrophe, hyphen or dot only between them
# ("O'Reilly", "e-mail", "Node.js"), and "++" or "#" only at its end ("C++", "F#").
_SAFE_WORD = re.compile(r"^[^\W_](?:[^\W_]|['\u2019.\-](?=[^\W_]))*(?:\+\+|#)?$")
# What a shell, a URL or a script needs, refused outright with any of them.
_UNSAFE_CHARS = set("|;&$`<>/\\~*=:!?%^@(){}[]\"\t\r\n,")
# Typed as a fix's first word with more after it, these run something.
COMMANDS = frozenset("""
sudo su sh bash zsh fish dash csh tcsh ksh env exec eval source xargs nohup time watch
curl wget ftp sftp scp ssh rsync nc ncat netcat telnet ping dig nslookup
rm rmdir mv cp ln dd mkfs fdisk diskutil chmod chown chflags chgrp touch mkdir cat tee
kill killall pkill shutdown reboot halt launchctl defaults security osascript open say
python python3 ruby perl php node deno bun npm npx pnpm yarn pip pip3 brew port gem cargo go
git svn hg docker podman kubectl helm terraform make cmake gcc clang java javac
echo printf export unset alias set cd ls ps top grep sed awk find vi vim nano emacs
""".split())
# And these do something even typed alone, with Return after them.
ALONE_COMMANDS = frozenset("reboot halt poweroff shutdown logout exit su sudo".split())
# The commonest words: a heard phrase made only of these would fire everywhere.
COMMON = frozenset("""
a about after again all also am an and any are as at back be because been but by can
could day did do does done for from get go going gonna good got had has have he her here
hey hi him his how i if in into is it its just know let lets like look make me more much
my need new no not now of oh ok okay on one only or other our out over people please
really right said say see she so some something sure take than thank thanks that the
their them then there these they thing things think this time to too two uh um up us
very want was way we well were what when which who will with would yeah yes you your
""".split())
SAFE_LONGER = 8          # letters a fix may add to what was heard, past twice its length
SAFE_LIKE = 0.5          # how alike their sounds (or spellings) must be, 0 to 1


def safe_fix(heard, meant):
    """None when a learned fix looks like a spelling fix, else why not (a few words,
    for the log and the panel). See above: nothing a nobody-typed fix could run."""
    raw = meant or ""                           # as it would be typed: a tab or a newline too
    heard, meant = " ".join((heard or "").split()), " ".join(raw.split())
    if not heard or not meant:
        return "empty"
    if unicodedata.normalize("NFKC", raw) != raw or any(unicodedata.category(c)[0] == "C" for c in raw):
        return "unusual characters"
    if set(raw) & _UNSAFE_CHARS or not all(_SAFE_WORD.match(w) for w in meant.split()):
        return "shell or link characters"
    words = meant.split()
    if (len(words) > 1 and words[0].lower() in COMMANDS) or meant.lower() in ALONE_COMMANDS:
        return "a command"
    if all(w.lower().strip("'\u2019.-") in COMMON for w in heard.split()):
        return "too common a phrase"
    h, m = spelled(heard), spelled(meant)
    if len(words) > len(heard.split()) + 1 or len(m) > max(2 * len(h), len(h) + SAFE_LONGER):
        return "longer than what was heard"
    ka, kb = sound_key(heard), sound_key(meant.replace("++", " plus plus").replace("#", " sharp"))
    like = max(difflib.SequenceMatcher(None, ka, kb, autojunk=False).ratio() if ka and kb else 0.0, alike(h, m))
    if like < SAFE_LIKE:
        return "doesn't sound like what was heard"
    return None


def guard_learned(rules):
    """(kept, [(heard, meant, why)] refused): the learned rules that may be typed."""
    kept, refused = [], []
    for heard, meant in rules:
        why = safe_fix(heard, meant)
        if why:
            refused.append((heard, meant, why))
        else:
            kept.append((heard, meant))
    return kept, refused


class Fixer(object):
    """The words file's fixes, applied to text for showing it (a transcript line,
    an export): the same whole-word, case-insensitive rules dictation uses, read
    again when the file changes, and each line's fixed text kept, since the view
    asks for the same lines over and over."""

    def __init__(self, path):
        self.path, self.mtime, self.rules, self.cache = Path(path), None, [], {}

    def _load(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime != self.mtime:
            self.mtime, self.cache = mtime, {}
            self.rules = [(compile_rule(h), m) for h, m in rules_in(self.path)] if mtime else []

    def apply(self, text):
        self._load()
        if not self.rules or not text:
            return text
        got = self.cache.get(text)
        if got is None:
            got = text
            for rx, meant in self.rules:
                got = rx.sub(lambda _m, s=meant: s, got)
            if len(self.cache) >= FIXED_CACHE:
                self.cache.clear()
            self.cache[text] = got
        return got


def compile_rule(heard):
    """A words file's rule as dictation applies it: whole words, case-insensitive,
    any spacing between them."""
    words = r"\s+".join(re.escape(w) for w in heard.split())
    return re.compile(r"(?<!\w)%s(?!\w)" % words, re.IGNORECASE)


def said(rules, alone=(), refused=()):
    """What the panel says after Fix Last Dictation…: the fix itself when there
    is one, or why nothing was learned."""
    if len(rules) == 1:
        return "Learned: %s → %s" % rules[0]
    if rules:
        return "Learned %d fixes" % len(rules)
    if refused:
        return "Not learned: “%s” doesn't look like a spelling fix. Add it in Edit Word Fixes… if you meant it" % refused[0][1]
    if alone:
        return "Not learned: “%s” is an everyday word" % alone[0]
    return "Nothing to learn: change the words it got wrong"


def counts(words, store=SUGGESTIONS):
    """For Standby's Settings, numbers only: {"count": the fixes in the words file
    (the user's own and the learned), "learned": those under "# learned",
    "suggested": how many the menu offers now (the same list, so the two always
    agree)}. A file not made yet is none; one that cannot be read, None."""
    try:
        lines = Path(words).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError:
        return None
    rule = lambda l: l.strip() and not l.strip().startswith("#") and "=>" in l and l.split("=>", 1)[0].strip()
    at = next((i for i, l in enumerate(lines) if l.strip().lower().startswith("# learned")), len(lines))
    return {"count": sum(1 for l in lines if rule(l)), "learned": sum(1 for l in lines[at + 1:] if rule(l)),
            "suggested": len(Learner(words, store=store).suggestions())}


# ---------------------------------------------------------------- the learner

def _load(path, default):
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
        return got if isinstance(got, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def prompt(path=VOCAB, limit=PROMPT_CHARS):
    """The user's own names for Whisper's prompt (the archival re-pass): the
    kept vocabulary, the strongest first, as a plain list ("Claude Code,
    Orion, Nadia Castellanos."). None before there is one; never rebuilt here."""
    names, size = [], 0
    for t in _load(path, {}).get("terms") or []:
        if not (isinstance(t, list) and t and isinstance(t[0], str) and t[0].strip()):
            continue
        if size + len(t[0]) + 2 > limit:
            break
        names.append(t[0].strip())
        size += len(t[0]) + 2
    return ", ".join(names) + "." if names else None


def echoes(text, names_prompt):
    """A segment that is only names from the prompt, two or more ("Claude
    Code, Orion."): Whisper repeating its prompt over silence, not speech."""
    if not names_prompt:
        return False
    names = {spelled(n) for n in names_prompt.rstrip(".").split(",")}
    pieces = [spelled(p) for p in re.split(r"[,.;]", text) if spelled(p)]
    return len(pieces) >= 2 and all(p in names for p in pieces)


class Learner(object):
    """For the dictation app: heard(text) after every dictation only
    queues it; one worker thread matches it against the vocabulary (rebuilt at
    most daily) and counts the pairs, and calls on_change() when a suggestion
    first comes up, so the menu can show it. The rest is for the menu, on the
    main thread: suggestions(), accept(), dismiss_all(), learn().

    words: the words file. folders(): the project folders (local_projects).
    vocab, store: where the vocabulary and the counts are kept."""

    def __init__(self, words, folders=local_projects, on_change=None, vocab=VOCAB, store=SUGGESTIONS,
                 authors=git_authors, repo=REPO, clock=time.time):
        self.words, self.folders, self.on_change = Path(words), folders, on_change
        self.vocab_path, self.store_path, self.authors, self.repo, self.clock = Path(vocab), Path(store), authors, repo, clock
        self.lock = threading.Lock()
        self.idx, self.built = None, 0.0
        self.jobs = None                     # a queue once started: until then heard() does nothing

    # -- the worker -----------------------------------------------------------
    def start(self):
        self.jobs = queue.Queue()
        threading.Thread(target=self._work, name="vocabulary", daemon=True).start()

    def heard(self, text):
        """After a dictation, its text (after the word fixes): queued, never kept."""
        if self.jobs is not None and text:
            self.jobs.put(text)

    def _work(self):
        next_build = self.clock() + FIRST_BUILD_AFTER
        while True:
            try:
                text = self.jobs.get(timeout=max(1.0, next_build - self.clock()))
            except queue.Empty:
                text = None
            try:
                if self.clock() >= next_build:
                    self.refresh()
                    next_build = self.clock() + REBUILD_EVERY
                if text:
                    self.heard_now(text)
            except Exception as exc:
                log("worker: %r" % exc)

    # -- the vocabulary ---------------------------------------------------------
    def refresh(self, force=False):
        """Rebuilt if older than a day (or forced); otherwise the kept one."""
        kept = _load(self.vocab_path, {})
        if not force and isinstance(kept.get("terms"), list) and self.clock() - float(kept.get("built") or 0) < REBUILD_EVERY:
            terms = [tuple(t) for t in kept["terms"] if isinstance(t, list) and len(t) == 2]
            built = float(kept["built"])
        else:
            started = time.time()
            terms, seen = build(self.folders(), self.words, self.authors, self.repo)
            built = self.clock()
            _save(self.vocab_path, {"built": built, "folders": seen, "terms": [list(t) for t in terms]})
            log("built: %d terms from %d folders in %.1fs" % (len(terms), seen, time.time() - started))
        with self.lock:
            self.idx, self.built = index(terms), built

    def heard_now(self, text):
        """Match one dictation and count what it found; True if a suggestion is new."""
        if self.idx is None:
            self.refresh()
        found = matches(text, self.idx or {})
        if not found:
            return False
        fixed = {h.lower() for h, _m in rules_in(self.words)}
        new = False
        with self.lock:
            data = self._store()
            for heard, term in found:
                key = "%s\t%s" % (heard.lower(), term)
                if key in data["dismissed"] or heard.lower() in fixed:
                    continue
                pair = data["pairs"].setdefault(key, {"heard": heard.lower(), "meant": term, "seen": 0})
                pair["seen"] += 1
                pair["at"] = round(self.clock())
                new = new or pair["seen"] == SHOW_AFTER
            if len(data["pairs"]) > MAX_PAIRS:
                order = sorted(data["pairs"], key=lambda k: (data["pairs"][k]["seen"], data["pairs"][k].get("at", 0)))
                for k in order[:len(data["pairs"]) - MAX_PAIRS]:
                    del data["pairs"][k]
            _save(self.store_path, data)
        log("heard %d sound-alike(s)%s" % (len(found), "; a suggestion is new" if new else ""))
        if new and self.on_change is not None:
            self.on_change()
        return new

    def _store(self):
        data = _load(self.store_path, {})
        pairs = data.get("pairs") if isinstance(data.get("pairs"), dict) else {}
        dismissed = data.get("dismissed") if isinstance(data.get("dismissed"), list) else []
        return {"pairs": {k: v for k, v in pairs.items() if isinstance(v, dict) and v.get("heard") and v.get("meant")},
                "dismissed": dismissed[-MAX_DISMISSED:]}

    # -- the menu's -------------------------------------------------------------
    def suggestions(self):
        """[(heard, meant, seen)]: heard at least twice, the most heard first."""
        with self.lock:
            pairs = self._store()["pairs"].values()
        got = [(p["heard"], p["meant"], int(p.get("seen") or 0)) for p in pairs if int(p.get("seen") or 0) >= SHOW_AFTER]
        return sorted(got, key=lambda s: (-s[2], s[0]))[:SHOWN]

    def accept(self, heard, meant):
        """A suggestion clicked: a learned rule from now on. The rules written."""
        written = self.learn([(heard, meant)])
        self._forget(["%s\t%s" % (heard.lower(), meant)], dismiss=False)
        return written

    def dismiss_all(self):
        """Dismiss All: the suggestions shown now are never offered again."""
        self._forget(["%s\t%s" % (h, m) for h, m, _n in self.suggestions()], dismiss=True)

    def _forget(self, keys, dismiss):
        with self.lock:
            data = self._store()
            for k in keys:
                data["pairs"].pop(k, None)
                if dismiss and k not in data["dismissed"]:
                    data["dismissed"].append(k)
            _save(self.store_path, data)

    def learn(self, rules):
        """Rules into the words file's learned section (add_learned)."""
        written = add_learned(self.words, rules)
        for h, m in written:
            log("learned: %s -> %s" % (h, m))
        return written
