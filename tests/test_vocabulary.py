"""Dictation learning the user's words, with made-up projects in
a temp folder: never the user's real ~/.claude or ~/.standby. How words sound;
the vocabulary from project folders (prose, not code; nothing from places macOS
would ask about); phrases that sound like a term but aren't spelled like it,
counted without keeping what was dictated; the words the user changed in Fix
Last Dictation… becoming rules in the words file's learned section, with their
own lines left alone; and the learner's thread."""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="sbvocab-"))
os.environ["STANDBY_ROOT"] = str(tmp / "root")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import vocabulary as v  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


HAVE_LIST = os.path.exists(v.WORD_LIST)

# How words sound.
same = [("cloud code", "Claude Code"), ("or ion", "Orion"), ("post grass", "Postgres"),
        ("pie web view", "pywebview"), ("and tropic", "Anthropic")]
check("sound-alikes share a key: cloud code / Claude Code, or ion / Orion, …",
      all(v.sound_key(a) == v.sound_key(b) for a, b in same), [(v.sound_key(a), v.sound_key(b)) for a, b in same])
check("  and are spelled closely", all(v.alike(a, b) >= v.MIN_LIKE for a, b in same))
check("  but the cloud is not Claude, nor code", v.sound_key("the cloud") != v.sound_key("Claude")
      and v.sound_key("code") != v.sound_key("Claude"))
check("spelling: case and punctuation aside", v.spelled("Claude-Code!") == "claude code")
if HAVE_LIST:
    check("everyday words: cloud, code, codes, deployed; not Claude, Orion, pywebview",
          all(v.ordinary(w) for w in ("cloud", "code", "codes", "deployed", "the"))
          and not any(v.ordinary(w) for w in ("Claude", "Orion", "pywebview")))
else:
    check("no word list: every word counts as everyday (phrases only)", v.ordinary("Claude"))

# The vocabulary, from a project's prose.
md = ("# Notes\n\nWe build on Claude Code and talk to Orion every day.\n"
      "Run `pywebviewInternal --flag` or:\n```\nSecretClass.method()\n```\n"
      "See https://example.com/Kubernetes for the cloud setup. Orion again.\n")
terms = v.prose_terms(md)
check("prose terms: Claude Code and Orion", "Claude Code" in terms and "Orion" in terms, terms)
check("  not code (backticks, fences) or links", not any(t in terms for t in ("pywebviewInternal", "SecretClass", "Kubernetes")), terms)
if HAVE_LIST:
    check("  nor everyday words", "cloud" not in terms and "setup" not in terms, terms)
check("names: a folder's parts without digits, not everyday ones", v.name_terms("Orion-api2-r51") == ["Orion"]
      and v.name_terms("@scope/orion-client") == ["orion-client"] + (["orion"] if HAVE_LIST else []), v.name_terms("@scope/orion-client"))

# Places macOS guards: read only if Standby's own code is there too.
home = v.HOME
check("guarded places: Documents readable when Standby runs from Documents",
      v.readable(home / "Documents" / "Projects" / "x", repo=home / "Documents" / "Projects" / "Standby"))
check("  but not Desktop, Downloads or another volume",
      not v.readable(home / "Desktop" / "x", repo=home / "Documents" / "S")
      and not v.readable(home / "Downloads" / "x", repo=home / "Documents" / "S")
      and not v.readable(Path("/Volumes/Disk/x"), repo=home / "Documents" / "S"))
check("  and Documents not either, with Standby elsewhere", not v.readable(home / "Documents" / "x", repo=home / "src" / "Standby"))
check("  anywhere else: readable", v.readable(tmp / "p", repo=home / "src" / "Standby"))

# A made-up project: CLAUDE.md, README, package.json, git history.
proj = tmp / "projects" / "orion-client"
proj.mkdir(parents=True)
(proj / "CLAUDE.md").write_text(md)
(proj / "README.md").write_text("Orion runs on Kubernetes. Kubernetes is the cluster.\n")
(proj / "package.json").write_text(json.dumps({"name": "@acme/orion-client"}))
git = ["git", "-C", str(proj), "-c", "user.email=t@example.com", "-c", "user.name=Nadia Castellanos", "-c", "commit.gpgsign=false"]
have_git = subprocess.run(["git", "-C", str(proj), "init", "-q"], capture_output=True).returncode == 0
if have_git:
    subprocess.run(git + ["add", "-A"], capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "first"], capture_output=True)
    check("git authors: from the folder's history", v.git_authors(proj) == ["Nadia Castellanos"], v.git_authors(proj))
check("  a folder that isn't a checkout: none, and no git run", v.git_authors(tmp, run=lambda *a, **k: 1 / 0) == [])
hidden = tmp / "guarded" / "secret-project"
hidden.mkdir(parents=True)
(hidden / "CLAUDE.md").write_text("Zanzibarian Zanzibarian\n")
words = tmp / "root" / "dictation-words.txt"
words.parent.mkdir(parents=True, exist_ok=True)
words.write_text("# mine\nCloud Code => Claude Code\nmy sequel => MySQL\n")
saved_guarded = v.GUARDED
v.GUARDED = saved_guarded + [tmp / "guarded"]
try:
    terms, seen = v.build([proj, hidden, tmp / "gone"], words)
finally:
    v.GUARDED = saved_guarded
names = [t for t, _w in terms]
check("vocabulary: the user's fixes first, then names, then prose seen twice",
      names[0] in ("Claude Code", "MySQL") and "orion-client" in names and "Kubernetes" in names and "Orion" in names, terms)
check("  one term whatever its case, written as a name (Orion, not orion); no everyday parts (client)",
      "orion" not in names and "client" not in names, names)
if have_git:
    check("  git authors in it", "Nadia Castellanos" in names, names)
check("  prose seen once left out (Claude Code is in by the user's fix)", "pywebview" not in names, names)
check("  nothing from a guarded folder, a gone one skipped", "Zanzibarian" not in names and seen == 1, (names, seen))

# Matching a dictation.
idx = v.index([("Claude Code", 10), ("Orion", 3), ("Postgres", 2), ("Anthropic", 3), ("Claude", 5), ("Rumi", 3)])
got = v.matches("Open cloud code, ask or ion about the post grass in the cloud. And tropic too.", idx)
check("matches: cloud code, or ion, post grass, and tropic",
      got == [("cloud code", "Claude Code"), ("or ion", "Orion"), ("post grass", "Postgres"), ("And tropic", "Anthropic")], got)
check("  not the cloud (a lone everyday word), nor what is spelled right",
      not v.matches("deploy to the cloud with Claude Code and Orion", idx), v.matches("deploy to the cloud with Claude Code and Orion", idx))
check("  nor a short name by chance (Rumi: too few sounds to tell)", not v.matches("roomy", idx))

# The user's own fixes.
check("Fix Last Dictation…: the changed words become a rule",
      v.diff_fixes("I use cloud code daily.", "I use Claude Code daily.") == ([("cloud code", "Claude Code")], []))
check("  words split wrongly: post grass => Postgres", v.diff_fixes("the post grass table", "the Postgres table") == ([("post grass", "Postgres")], []))
check("  punctuation kept off the rule", v.diff_fixes("talk to or ion.", "talk to Orion.") == ([("or ion", "Orion")], []))
if HAVE_LIST:
    check("  a lone everyday word takes a real neighbour along", v.diff_fixes("cloud code is up", "Claude code is up")
          == ([("cloud code", "Claude code")], []))
    check("  but never just \"the\": not learned, and said why",
          v.diff_fixes("ask the cloud", "ask the Claude") == ([], ["cloud"])
          and v.said([], ["cloud"]) == "Not learned: “cloud” is an everyday word")
check("  words added or removed: edits, nothing learned", v.diff_fixes("send it now", "send it right now please") == ([], []))
check("  a rewrite: nothing learned", v.diff_fixes("one two three four five", "six seven eight nine ten") == ([], []))
check("  what the panel says", v.said([("a b", "C")]) == "Learned: a b → C" and v.said([("a", "b"), ("c", "d")]) == "Learned 2 fixes"
      and v.said([]) == "Nothing to learn: change the words it got wrong")

# The learned section.
before = words.read_text()
check("learned: written under its own heading, after the user's lines",
      v.add_learned(words, [("or ion", "Orion"), ("cloud code", "Claude Code")]) == [("or ion", "Orion")])
text = words.read_text()
check("  the user's lines exactly as they were, and theirs win (cloud code)",
      text.startswith(before.rstrip("\n")) and text.count("Cloud Code") == 1 and v.LEARNED in text, text)
v.add_learned(words, [("or ion", "Orión"), ("post grass", "Postgres")])
text = words.read_text()
check("  a learned rule for the same words is replaced; others added",
      "or ion => Orión" in text and "or ion => Orion\n" not in text and "post grass => Postgres" in text
      and text.count(v.LEARNED) == 1, text)
check("  swapped in whole: no temp file left", not any(p.name.endswith(".tmp") for p in words.parent.iterdir()))
check("  every rule reads back", ("post grass", "Postgres") in v.rules_in(words) and ("my sequel", "MySQL") in v.rules_in(words))

# The learner, on made-up folders and a clock of its own (and a words file of its own).
words = tmp / "root" / "learner-words.txt"
words.write_text("# mine\nCloud Code => Claude Code\n")
now = [1000000.0]
asked = []
store, vocab = tmp / "root" / "sugg.json", tmp / "root" / "vocab.json"
changes = []
L = v.Learner(words, folders=lambda: (asked.append(1), [proj])[1], on_change=lambda: changes.append(1),
              vocab=vocab, store=store, clock=lambda: now[0])
L.refresh()
check("learner: builds the vocabulary from the folders, and keeps it", asked == [1] and vocab.exists()
      and "Orion" in [t for t, _w in json.loads(vocab.read_text())["terms"]])
L.idx = None
L.refresh()
check("  within a day: the kept one, the folders not read again", asked == [1] and L.idx)
now[0] += v.REBUILD_EVERY + 1
L.refresh()
check("  a day on: rebuilt", asked == [1, 1])
L.idx = v.index([("Orion", 3), ("Anthropic", 3)])
said = "Remind or ion that the budget is 4200 dollars."
check("heard once: counted, not offered yet", L.heard_now(said) is False and L.suggestions() == [] and changes == [])
check("  heard twice: offered, and the menu told once", L.heard_now("ask or ion") is True and changes == [1]
      and L.suggestions() == [("or ion", "Orion", 2)], L.suggestions())
L.heard_now("or ion again")
check("  a third time: no new news", changes == [1] and L.suggestions()[0][2] == 3)
cw = tmp / "counted-words.txt"
cw.write_text("# how to write a fix\ncloud code => Claude Code\nor ion => Orion\n\n" + v.LEARNED
              + "\nand tropic => Anthropic\n# a note\n")
check("counts for Settings: every fix, the learned ones, and what the menu offers now",
      v.counts(cw, store=store) == {"count": 3, "learned": 1, "suggested": len(L.suggestions())}
      and len(L.suggestions()) == 1, v.counts(cw, store=store))
check("  a words file not made yet: none of each", v.counts(tmp / "none-yet.txt", store=store)["count"] == 0
      and v.counts(tmp / "none-yet.txt", store=store)["learned"] == 0)
check("  numbers only: nothing of the words", all(isinstance(x, int) for x in v.counts(cw, store=store).values()))
kept = store.read_text()
check("  only the pair and its count are kept, never what was dictated",
      "budget" not in kept and "4200" not in kept and "Remind" not in kept and "or ion" in kept, kept)
L.heard_now("and tropic"); L.heard_now("and tropic")
check("Dismiss All: what is shown goes, and stays gone",
      (L.dismiss_all(), L.suggestions())[1] == [] and (L.heard_now("and tropic"), L.heard_now("or ion"), L.suggestions())[2] == [])
L2 = v.Learner(words, folders=lambda: [], vocab=vocab, store=tmp / "root" / "sugg2.json", clock=lambda: now[0])
L2.idx = v.index([("pywebview", 3)])
L2.heard_now("pie web view"); L2.heard_now("pie web view")
offered = L2.suggestions()
if offered:
    heard, meant, _n = offered[0]
    check("accept: a learned rule from now on, and no longer offered",
          L2.accept(heard, meant) == [(heard, meant)] and (heard, meant) in v.rules_in(words) and L2.suggestions() == [])
    L2.heard_now("pie web view")
    check("  words the file already fixes are not counted again", L2.suggestions() == [])
else:
    check("accept: (a sound-alike to accept)", False, L2.suggestions())
L3 = v.Learner(words, folders=lambda: [], vocab=vocab, store=tmp / "root" / "sugg3.json", clock=lambda: now[0])
L3.idx = {}
many = {"pairs": {"w%d\tT%d" % (i, i): {"heard": "w%d" % i, "meant": "T%d" % i, "seen": 1, "at": i} for i in range(60)}, "dismissed": []}
v._save(L3.store_path, many)
L3.idx = v.index([("Orion", 3)])
L3.heard_now("or ion")
check("  at most %d pairs kept, the least heard and oldest dropped" % v.MAX_PAIRS,
      len(json.loads(L3.store_path.read_text())["pairs"]) == v.MAX_PAIRS
      and "or ion\tOrion" in json.loads(L3.store_path.read_text())["pairs"])

# The thread: heard() only queues; before start() it does nothing.
(tmp / "root" / "words4.txt").write_text("")
L4 = v.Learner(tmp / "root" / "words4.txt", folders=lambda: [proj], vocab=tmp / "root" / "vocab4.json", store=tmp / "root" / "sugg4.json")
L4.heard("or ion")
check("before start(): heard() does nothing", L4.jobs is None and not (tmp / "root" / "sugg4.json").exists())
L4.start()
L4.heard("or ion"); L4.heard("or ion")
deadline = time.time() + 10
while time.time() < deadline and not L4.suggestions():
    time.sleep(0.05)
check("  started: matched on its own thread (the vocabulary built first)", L4.suggestions() == [("or ion", "Orion", 2)],
      L4.suggestions())
# What the meeting's own Claude answers, read; and the fixes, applied for showing.
answer = ("Sure:\n- P X R => PXR\n2. `cooper netties` => `Kubernetes`\ncloud => Claude\n"
          "the whole thing was said wrong => something else entirely here now\npxr => PXR\nsame => same\n"
          "SUGGEST: what about the budget?\n---\nx => y")
terms = v.parse_terms(answer)
check("the answer's heard => meant lines, bullets, numbers and quotes allowed",
      terms == [("P X R", "PXR"), ("cooper netties", "Kubernetes"), ("cloud", "Claude"), ("pxr", "PXR")], terms)
check("  not a rewrite, nor what changes nothing, nor anything below ---, nor NONE",
      v.parse_terms("NONE") == [] and ("x", "y") not in terms)
kept, dropped = v.guard_terms(terms)
if HAVE_LIST:
    check("  a lone everyday word left out; a phrase, or a word that isn't everyday, kept",
          dropped == [("cloud", "Claude")] and ("pxr", "PXR") in kept and ("P X R", "PXR") in kept, (kept, dropped))
fx_file = tmp / "root" / "fixer-words.txt"
fx_file.write_text("cooper netties => Kubernetes\n")
fx = v.Fixer(fx_file)
check("Fixer: the file's rules, applied as dictation does (whole words, any case)",
      fx.apply("Deploy to Cooper  Netties now") == "Deploy to Kubernetes now" and fx.apply("netties") == "netties")
time.sleep(0.01)
fx_file.write_text("cooper netties => K8s\n")
os.utime(fx_file, (time.time() + 5, time.time() + 5))
check("  a change to the file: read again, the kept lines forgotten", fx.apply("Deploy to Cooper  Netties now") == "Deploy to K8s now")
check("  no file: the text as it is", v.Fixer(tmp / "nope.txt").apply("cooper netties") == "cooper netties")
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
