# Contributing to Standby

Thanks for your interest. Standby is **open source, not open contribution**, for now: the code is MIT and yours
to read, run and fork, but changes are made by a small team, and pull requests are only taken when they were
agreed in an issue first.

So: **open an issue before any change.** Bugs, rough edges and ideas are all welcome there. If you'd like to fix
something yourself, say so in the issue, and wait for a yes before you write the code; a pull request that
arrives without one will most likely be closed, however good it is.

## Reporting a bug

Use the **Bug report** form. It asks for what helps most: your macOS version, the chip, Standby's version (the
commit, `git -C <your standby folder> rev-parse --short HEAD`), your Claude Code version (`claude --version`), the
steps, and what you expected. The logs are in `~/.standby`: `ui.log` (the app), `daemon.log` (the recorder) and
`dictation.log`. Read them before attaching: they hold no audio, but can hold words from your meetings.

Security issues go privately, never in an issue: see [SECURITY.md](SECURITY.md).

## Development setup

You need what a user needs (README, Requirements): an Apple Silicon Mac on macOS 14 or later, Python 3.12, and
Claude Code signed in.

```bash
git clone https://github.com/mksadoughi/standby.git
cd standby
./install.sh            # the app, its Python environment in ~/.standby/venv, the models, the plugin
```

The apps run from this folder in place, so a change to `mac/` shows in Standby once you quit and reopen it, and a
change to `lite/` in a new Claude Code session (or `/reload-plugins`).

The layout is in the README, under **How it's built**.

## Running the tests

```bash
PY=~/.standby/venv/bin/python bash tests/run_all.sh                       # the fast suites, in parallel
PY=~/.standby/venv/bin/python bash tests/run_all.sh test_voice test_ui    # just these
PY=~/.standby/venv/bin/python STANDBY_TESTS=full bash tests/run_all.sh     # also the ones that need a real window
```

- The fast suites need no microphone, speaker, model or window, and never touch your own `~/.standby`: each runs
  with a folder of its own and no devices. They are what CI runs.
- Every `tests/test_*.py` and `tests/test_*.sh` is listed in `tests/suites.txt` (name, how it runs, and `par` or
  `full`); `run_all.sh` refuses to run if one isn't. Add a new suite there.
- Nothing in a test may play sound or open the microphone. Tests that need the real world (a window, the
  speaker models) are `full` and run one at a time.
- Run the suites your change touches before you open a pull request, and say which in it.

## Commits

- One change per branch, off `main`, named for what it changes.
- Commit messages say what changed and why, in the product's own words: "The orb pauses mid-sentence", not
  "fix bug". No ticket numbers or internal names.
- **Commits are dated in UTC.** Commit and merge with `TZ=UTC` (`TZ=UTC git commit`, `TZ=UTC git merge`),
  so no commit carries a local time zone.
- Code reads like the code around it: the same naming, comment density and idiom.

## Releases

1. Move the **Unreleased** notes in [CHANGELOG.md](CHANGELOG.md) under the new version and date.
2. Bump `"version"` in `lite/.claude-plugin/plugin.json` to the same version: the plugin's version changes with
   every release tag, and only then.
3. Commit (`TZ=UTC`), tag `vX.Y.Z` on `main`, push the tag, and publish the GitHub release with the changelog's
   notes.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE), as the rest of
Standby is.
