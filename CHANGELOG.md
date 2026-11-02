# Changelog

All notable changes to Standby are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-01

Calls work smoothly, your permissions survive a reinstall, and setup is easier, with steadier meetings and dictation
too.

### Added

- **Set up Standby.** One window before your first call or meeting. It asks only for what that call or meeting
  needs, when it needs it, then starts the call once. If a start fails, it tells you why in one sentence and what to
  do next.
- **Settings > Permissions.** What macOS lets Standby and Standby Dictation use, shown live, with one way to fix
  each.
- **Pictures on a call.** Claude can show what it's talking about, such as a diagram or a formula. It appears as a
  card above the mini window and in the main window, sandboxed, and you can turn it off in Settings > General.
- **Choices on a card.** Click one to answer, and your pick stays marked. New cards come in order, and › says New
  while more wait. Command-C and Command-A work inside a card.
- **The voice's model.** Settings > Voice lets you pick the model (Haiku, Sonnet or Opus) and how much it thinks, and
  says when the model you chose isn't available and what runs instead. A model you saved stays yours.
- **What opens when a call starts.** Settings > General picks the mini window (the default), the full window or
  nothing.
- **Hide.** The first button in the mini window's row puts it away for that call while the call goes on. It comes
  back from the band, from /standby window, or when the voice shows you something.
- **A meeting that ends outside Claude Code.** Your session is told, once.
- **The website.** A new hero and film: a call about a landing page that changes as you talk, then a meeting about
  its launch. The site reads better on phones. It counts visits with GoatCounter, which uses no cookies and keeps no
  personal data.

### Changed

- **The voice and your session work as one.** The voice hands work to the session with what you said, the session
  tells the voice when something matters, and the voice decides what to say. It always says a few words when it
  takes something deeper, and when you ask several things at once, it answers what's quick and takes the rest
  deeper in the same turn.
- **The voice runs on Sonnet at Low thinking** by default, the fastest to answer. A level you chose stays yours.
- **The voice knows how long ago** each task was asked or finished, and says it the way a person would.
- **A call opens with a short hello,** nothing more. Its voice starts fresh each time, from your session's summary.
- **Connecting** that takes more than a few seconds says why.
- **Calls speak in Alba** by default. A voice you chose stays yours.
- **The orb and the band say what's going on.** While the session works on something you handed over, the orb shows
  it, with "Working on it" as its tip. The band says what the session is working on, and names the session the call
  is with ("On a call with Planner").
- **The mini window.** A call opens in the mini window by itself (before, nothing opened), and a call put away
  waits there (it was called the orb). The orb shows the call's
  state by its motion, and the call's name and its buttons roll out when you point at it, in words: Pause (Resume,
  with a bell when something is waiting for you), End, Full window, and Mini window, the one you're in. It follows
  you to another display. Standby's window is an ordinary window now: other apps can go in front of it.
- **Settings** opens in its own window and is organised by what you set, as in System Settings: General, Voice,
  Sound, Meetings, Dictation, Permissions and Usage (before: Call, Recording, Dictation, Permissions and Usage).
  Settings > Usage shows input and output tokens separately.
- **A meeting's sound** comes through macOS's System Audio Recording permission only. Standby never asks for screen
  recording.
- **Your folders.** macOS never asks Standby for your Documents or project folders: a call's Claude Code is started
  by your own Claude Code. Standby Dictation never asks for your Documents folder either.
- **Reinstalling keeps your permissions,** because the apps are signed with a certificate made on your Mac.
- **The first call or meeting starts as fast as later ones:** the installer loads and downloads what they need
  ahead of time.
- **No Homebrew or password needed:** a Mac without Python 3.12 gets a ready-made one of Standby's own.
- **The installer** finds Claude Code in `~/.local/bin` and says plainly when Claude Code is too old for the plugin.
- **Standby Dictation's setup** is one clear sequence: Microphone, Accessibility, then try it. Input Monitoring is
  asked for only if you choose a double-tap shortcut. Each step is ticked as soon as it's allowed, and the setup
  shows where the app is if you need to add it by hand.
- **A meeting's transcript** is treated as what people said, never as instructions to follow.
- **Privacy in logs.** Routine diagnostics omit conversation text, generated titles, task instructions and learned
  word fixes. Opt-in debug call traces keep their detail in the session's own folder. Existing logs aren't erased.
- **Mac checks.** GitHub Actions tests macOS 15 and 26 and can be started manually once the repository is public.

### Fixed

- **Work handed on during a call is never lost,** results come back whole, and Resume after a pause tells you what
  came back at once. If a task ends with nothing to tell, the voice says so.
- **The voice no longer hears itself,** and background noise, outdoors or in the room, no longer counts as you
  talking. Words you start just as the voice finishes are kept.
- **The voice keeps going when you switch mics or headphones,** and headphones you plug in are used straight away.
- **A noise at a call's start** no longer cuts its hello.
- **The voice says decimals** with their point.
- **A call ends when the Claude Code it's with quits,** and a long pause no longer ends a call.
- **Calls leave no extra conversations** in your Claude Code session list.
- **A call's Claude Code never starts in your home folder.**
- **Signed out of Claude Code:** Standby tells you to run /login instead of starting the call.
- **Slow networks.** Standby starts quickly even where name lookups hang (this used to take about 35 seconds).
- **A quiet voice in a meeting** is heard.
- **Switching a meeting's sound source** takes one click.
- **A meeting app you've switched off** but that still plays on your speakers doesn't come back in through the mic.
- **Dictation (F5) works during a call.**

## [0.1.0] - 2026-10-23

The first public release: the Claude Code you already use, with ears and a voice, on your Mac.

### Added

- **Meetings from Claude Code.** `/standby listen` (or just asking Claude) records any meeting app, or the room
  you're in, with no bot in the call. The meeting goes to the same Claude Code session with your questions, or as it
  goes; when it ends, its summary is there. Speakers are labelled, and voices you name are known next time.
- **Calls.** `/standby call` and talk: a quick voice answers what it can on the spot and hands real work to your
  Claude Code session, which does it in the chat while you keep talking. You can talk over it, pause it from the
  mini window, the line above the prompt or the window, and keep or forget its summary at the end.
- **Standby's window and the mini window.** Live transcripts with names, Pause and End; past meetings and calls; a
  call put away waits in a mini window over any app.
- **Dictation in any app.** Press F5, speak, and the text lands where your cursor is, punctuated. Word fixes, and
  the words your meetings taught it.
- **On-device speech.** Parakeet speech-to-text (25 languages, or English), speaker diarization, and Standby's own
  voice (Pocket TTS), all on your Mac; only text goes to your Claude Code.
- **Settings and Usage.** Recording, microphone, speech model, voice, dictation, and what Standby's own models did
  (calls and tokens).
- **The installer** (`./install.sh`): a private Python environment, the models, the apps in `~/Applications`, the
  `standby` command, and the Claude Code plugin for every folder.

[Unreleased]: https://github.com/mksadoughi/standby/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mksadoughi/standby/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mksadoughi/standby/releases/tag/v0.1.0
