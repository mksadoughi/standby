# Standby: a guide for AI agents

You are an AI agent (Claude Code or another) and your user wants Standby. This guide tells you what Standby is, how
to install it on their Mac, how to use every part of it for them, how to fix common problems, and how to remove it.
Follow it step by step. If anything here disagrees with what Standby itself says (`/standby help`,
`/standby settings`, `standby help`), trust Standby: it changes faster than this page.

Repository: https://github.com/mksadoughi/standby · License: MIT · Free, with no account of its own.

## What Standby is

Standby is a Claude Code plugin with a small Mac app behind it. It gives the user's own Claude Code session ears and
a voice:

- **Calls.** The user talks with their Claude Code session by voice. A fast voice model (Haiku by default) answers in
  speech and hands real work to the same session, which does it in the chat while they keep talking.
- **Meetings.** Standby hears the user's meetings from the Mac (any meeting app, a call in a browser, or people in the
  room) with no bot in the call, and passes the transcript to the session so the user can ask about it.
- **Dictation.** Press a key in any app, speak, and the text is pasted where the cursor is.

Speech-to-text, speaker labels and the voice run on the Mac. The user's words go to their Claude Code as text (with
anything they paste or attach), and Claude Code talks to
its model provider as it always does. Standby uses the user's own Claude Code sign-in: there is no separate API key.

It works in Claude Code sessions on the same Mac: the terminal, the VS Code extension and the Claude desktop app.
It does not work in cloud sessions (claude.ai/code), because the plugin talks to the Standby app on the Mac.

## 1. Check the Mac first

Ask the user before installing anything. Then check each requirement and tell them plainly if one fails.

| Requirement | Check |
|---|---|
| Apple Silicon | `uname -m` prints `arm64` |
| macOS 14 (Sonoma) or later | `sw_vers -productVersion` |
| 6 GB free disk (fresh install) | `df -h ~` |
| Python 3.12 (3.13 also works) | `python3.12 --version`. If missing, the installer adds it with Homebrew (`brew`), or stops if Homebrew isn't there |
| Claude Code installed and signed in | `claude --version`, and the user has run `claude` and signed in |
| Memory | `sysctl -n hw.memsize`. With 8 GB, Standby uses its small English speech model; 16 GB is recommended for calls |

## 2. Install

Clone into a folder the user will keep: Standby's apps run from this folder, so it must not be deleted or moved
later. Install from the `main` branch of a normal clone, not from a git worktree or another branch (the installer
refuses those).

```bash
git clone https://github.com/mksadoughi/standby.git ~/standby
cd ~/standby && ./install.sh
```

The installer takes a few minutes. It is safe to run again. It:

- makes a private Python environment in `~/.standby/venv` and installs its pinned packages;
- downloads the on-device models once (speech model about 0.5 GB on an 8 GB Mac, about 2.5 GB otherwise; speaker,
  turn-taking and speech-detection models about 240 MB; Standby's voice, about 240 MB, comes the first time a call
  starts);
- adds the `standby` command in `~/.local/bin` (and adds that folder to `PATH` in `~/.zshrc` if needed);
- builds `~/Applications/Standby.app` and `~/Applications/Standby Dictation.app`;
- adds the Claude Code plugin for every folder:
  `claude plugin marketplace add <this folder>` then `claude plugin install standby@standby --scope user`.
  If that step fails, the installer prints those two commands to run by hand.

It ends with a "Done" block. Then verify:

```bash
standby check                          # every row should say OK
open ~/Applications/Standby.app        # or: standby app
```

In Claude Code, start a new session (or run `/reload-plugins` in one already open) and run `/standby help`. A list
of Standby's commands means the plugin is loaded.

Tell the user what macOS will ask, so they say yes:

- **Microphone**, for "Standby", the first time it records.
- **Screen & System Audio Recording**, the first time a meeting records what the Mac plays. Standby takes the sound
  only, never the screen. After allowing it, Standby must be quit and reopened; a paused meeting resumes.
- **Microphone, Input Monitoring and Accessibility**, for "Standby Dictation", when dictation is turned on: to hear
  them, to notice the shortcut, and to paste the text.

## 3. Use it

Standby works from inside Claude Code. When the plugin loads, you get its tools and a short skill. The user can also
type `/standby` commands, or use the line Standby shows above the prompt (the band).

### Your tools

| Tool | What it does |
|---|---|
| `mcp__standby__call` | Start a voice call with the user |
| `mcp__standby__end_call` | End the call |
| `mcp__standby__listen` | Start listening to the user's meeting. `pass: "live"` only if they ask for the transcript as it goes |
| `mcp__standby__stop` | Stop listening. The summary shows to the user and stays in Standby; it isn't sent to you |
| `mcp__standby__meeting` | Read the transcript of the latest meeting for this session, live or ended, when it isn't already in the chat |
| `mcp__standby__settings` | With no arguments, list settings with their values and choices; with `setting` and `value`, change one |

Rules:

- Start a call or listening only when the user asks. When you call `call` or `listen`, Standby rings the user on the
  band first; nothing starts until they accept (they can choose "Always accept", which sets `auto_accept`).
- Change a setting only when the user asks.
- Tell the user, in one line, what you did.
- Recording people is regulated. Remind the user to tell others when a meeting is transcribed; in many places
  everyone has to agree.

### The user's commands

| Command | What it does |
|---|---|
| `/standby call` | Start a voice call (`/standby end` to hang up) |
| `/standby listen` | Listen to the meeting; the transcript goes with the user's prompts |
| `/standby listen live` | Listen, and pass the transcript to Claude as it goes |
| `/standby pause` / `/standby resume` | Pause or resume listening |
| `/standby stop` | Stop listening; the summary shows on the band |
| `/standby name Speaker 1 Nadia` | Name a speaker; Standby recognizes that voice in later meetings |
| `/standby window` | Open Standby's window on this session |
| `/standby settings` | List the settings |
| `/standby set <name> <value>` | Change a setting, for example `/standby set transcript live` |
| `/standby off` | Turn Standby off for this session |
| `/standby help` | The list |

Plain words work too: the user can just ask you to call them, listen to the meeting, or stop.

### The band above the prompt

While a call or a meeting is on, Standby shows a line above the prompt with its actions. In the terminal each action
is a letter, typed alone followed by Enter. In VS Code and the desktop app they are links.

| When | Terminal | VS Code / desktop app |
|---|---|---|
| Listening to a meeting | `p` pause or resume, `e` end, `o` full window | Pause, End, Full window |
| On a call | `p` pause or resume, `e` end, `m` mini window, `o` full window | Pause, End, Mini window, Full window |
| Standby rings (Claude asked to start) | `a` accept, `d` decline, `y` always accept | Accept, Decline, Always accept |
| Confirming the end | `e` end, `k` keep going | End, Keep talking / Keep listening |
| After a meeting | `c` close the summary | Close |
| After a call | `a` add to Claude Code, `f` forget | Add to Claude Code, Forget |

### Calls

- The voice greets the user briefly and has the session's recent context.
- When the user asks for real work, the voice hands it to this session. It arrives as the user's own message, marked
  "from your call". Do it as you would any request; the voice tells the user how it's going and what came back. If
  you are busy, the hand-off waits its turn and starts when your current work ends.
- What the user types in the chat during the call is passed to the voice too.
- Pause silences the voice and holds what it would say; the session's work goes on. A red dot on Resume means
  something is waiting. End asks once to confirm. The voice can also end the call when the user says goodbye.
- The call can be shown in Standby's full window (the conversation, Pause, End) or put away in the **mini window**,
  a small round window in the corner of the screen, above every app. Clicking the mini window pauses or resumes the
  call; pointing at it shows Full window, Settings and End; double-clicking opens the full window.
- After the call, the band shows a short summary with **Add to Claude Code** (a quiet note to this session with what
  it didn't know yet) or **Forget**.

### Meetings

- **When you see the transcript.** By default (`transcript: ask`) it comes with each of the user's prompts, labelled
  `[Standby · meeting · hh:mm-hh:mm]`: all of it the first time, then what's new. With `live` it arrives as notes as
  the meeting goes. A note `[Standby · meeting started · hh:mm]` tells you a meeting is on. Answer questions about it
  in the chat; use `mcp__standby__meeting` when the transcript isn't already here (after the meeting ended, say).
- **What it hears.** Standby's meeting window shows a chip for each source it hears (the user's mic, Zoom, a browser,
  any app playing). The user can untick a source for that meeting: nothing of it is transcribed or reaches you. An app
  joins once it has played for about 2 seconds; every source starts on again at the next meeting.
- **Who's speaking.** Lines are labelled by speaker. The user names one with `/standby name Speaker 1 Nadia` or by
  clicking the label in the window; named voices are recognized in later meetings, kept only on the Mac.
- **Ending.** Stopping shows a summary on the band; it stays in Standby. A meeting with nothing said for 15 minutes
  (setting `auto_stop`) stops itself.
- Past meetings and calls are listed in Standby's window, where they can be renamed, copied, saved as Markdown or
  deleted. Only one thing runs at a time: end a call before listening to a meeting.

### Dictation

- Off until the user turns it on: `standby dictation on`, or Settings > Dictation in Standby.
- The default shortcut is F5 (the microphone key). Others: Control twice, Fn twice, Right or Left Command twice,
  Right Option twice, Hold to Talk, or a custom key, all in Settings > Dictation or the menu-bar icon.
- Press, speak, press again: the text is pasted where the cursor is (or typed, if the user prefers).
- **Word fixes** (`~/.standby/dictation-words.txt`) correct names and terms; "Fix Last Dictation…" in the menu-bar
  menu learns a correction. Standby also learns words from the user's Claude Code projects.
- `standby dictation` shows its state and which permissions are missing; `standby dictation off` turns it off.

### Settings

List them with `mcp__standby__settings` or `/standby settings`; change one only when the user asks.

| Setting | Choices | Default |
|---|---|---|
| `transcript` | `ask`, `live` | `ask` |
| `capture` | `auto` (mic and what the Mac plays), `mic`, `meeting` | `auto` |
| `mic` | `default` or a microphone's name | `default` |
| `speech_model` | `small`, `standard` | `small` on 8 GB Macs, else `standard` |
| `english_only` | `on`, `off` | from the Mac's language |
| `voice` | `automatic` or one of Standby's voices | `automatic` |
| `voice_model` | `haiku`, `sonnet`, `opus` | `haiku` |
| `model` | `haiku`, `sonnet`, `opus` (Standby's own meeting agent) | `opus` |
| `talk_over` | `on`, `off` (interrupt the voice on laptop speakers) | `off` |
| `speakers` | `on`, `off` (label speakers) | `on` |
| `remember_voices` | `on`, `off` (remember named voices and the user's own) | `on` |
| `auto_accept` | `on`, `off` (Claude starts calls and listening without asking) | `off` |
| `ai_update` | 1 to 200 sentences | `10` |
| `auto_stop` | 0 to 600 minutes, 0 is never | `15` |

Settings > Usage in Standby's window shows what Standby's own models used, by model and kind.

## 4. When something's wrong

| Problem | Fix |
|---|---|
| `/standby` isn't there | Start a new Claude Code session or run `/reload-plugins`. Still missing: run `./install.sh` again, or `claude plugin marketplace add ~/standby && claude plugin install standby@standby --scope user` |
| "Standby can't listen here: this Claude Code doesn't save its conversation" | This Claude Code was started from another one. Start it with `env -u CLAUDE_CODE_CHILD_SESSION claude` |
| "Standby isn't running" | `open ~/Applications/Standby.app` (or `standby app`) |
| "This Standby doesn't have that yet: update it" | Update (section 5) |
| The transcript stays empty | System Settings > Privacy & Security > Microphone: allow Standby. Check the `mic` setting |
| The other side of a call is missing | System Settings > Privacy & Security > Screen & System Audio Recording: allow Standby, then quit and reopen it |
| "Claude can't sign in" | Run `claude`, then `/login` (or check the API key or cloud credentials Claude Code uses) |
| "Your Mac is with …: stop it there first" | Another recording is running; stop it first |
| The voice isn't ready | Standby's voice downloads the first time a call starts (about 240 MB); wait a minute, or try it in Standby's Settings > Call > Voice > Try |
| Dictation does nothing | `standby dictation` lists what's missing; the menu-bar icon has a button for each |
| Anything else | `standby check`, `standby status`, and the logs: `~/.standby/ui.log`, `~/.standby/daemon.log`, `~/.standby/dictation.log` |

## 5. Update

```bash
cd ~/standby && git pull && ./install.sh
```

Then quit and reopen Standby, and start a new Claude Code session (or `/reload-plugins`). Meetings, calls and
settings are kept.

## 6. Uninstall

Ask the user first, and tell them this deletes their meetings and calls: copy `~/.standby/sessions` to keep them.

```bash
standby dictation off
claude plugin uninstall standby@standby && claude plugin marketplace remove standby
rm -rf ~/.standby ~/Applications/Standby.app "$HOME/Applications/Standby Dictation.app"
rm -f ~/.local/bin/standby ~/.local/bin/standby-ctx
```

Then, if the user wants everything gone:

- the models in the Hugging Face cache, `~/.cache/huggingface/hub`: the folders whose names contain `parakeet`,
  `whisper-large-v3-turbo` or `pocket-tts` (only if nothing else uses them);
- the `PATH` line for `~/.local/bin` that the installer may have added to `~/.zshrc`;
- the cloned folder itself, for example `~/standby`.

## Where things live

- `~/.standby/sessions/` meetings and calls (transcripts, summaries, recordings), on the Mac only; `standby purge
  <session>` deletes one.
- `~/.standby/config.json` settings; `~/.standby/agent/usage.jsonl` usage counts (no meeting text).
- Voice prints, on the Mac only: the voices the user names, and the user's own voice, which Standby learns from calls
  to tell them apart (setting `remember_voices`; Settings > Recording has Forget for the user's voice and for all).
- Standalone dictation isn't kept as a session. When it uses a running recorder, temporary audio and text files
  can remain after an interruption. Dictated questions within a meeting or call stay in that session.
- Routine logs omit conversation text. Opt-in debug call traces contain words and replies in the session's folder.
  Older logs can contain conversation text; diagnostics can include local paths, device names and error details.
- Deleting a meeting or call removes its session files, delivered copies and debug trace, not global diagnostic
  logs, what was already sent to Claude Code, or exports.
- Standby has no account and no telemetry. It downloads only its models, from Hugging Face and GitHub.
