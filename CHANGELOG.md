# Changelog

All notable changes to Standby are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The mini window.** A call put away waits in the mini window (it was called the orb). Point at it for its
  buttons, in words: Pause (Resume, with a bell when something is waiting for you), End, Full window, and Mini
  window, the one you're in. Standby's window is an ordinary window now: other apps can go in front of it.

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

[Unreleased]: https://github.com/mksadoughi/standby/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mksadoughi/standby/releases/tag/v0.1.0
