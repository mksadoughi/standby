# Standby inside Claude Code

## Install

`install.sh` adds it to Claude Code for every folder: this repo is a plugin marketplace, and the
plugin is `standby@standby`. By hand (Claude Code 2.1.287 or later):

    claude plugin marketplace add <this repo> && claude plugin install standby@standby --scope user

or, inside Claude Code: `/plugin install standby --marketplace <this repo>`.

To update: `git pull` in this repo. The plugin loads in place from `lite/`, so a new Claude Code
session has it, or `/reload-plugins` in one already open (no `/plugin update` needed). Restart
Standby too when its app changed.

## How to try it

1. Type `/standby` in Claude Code (signed in, Standby running). Nothing shows above the prompt while nothing is live.
2. During a call or a video, type `/standby listen` (or ask Claude to listen): a quiet line above the prompt reads "● Listening · … 1m", then Pause, End | Full window (in the terminal, type the letter shown and press Enter), with the latest finished line of the meeting dim under it. Full window opens Standby on this meeting; `/standby pause` and `/standby stop` work too. To name a speaker here: `/standby name Speaker 1 Nadia`.
3. Ask Claude anything about the meeting in the normal chat. By default the transcript goes with your prompt (all of it the first time, then what's new), so Claude knows without you pasting anything, and nothing fills the chat while you only listen. After the meeting, Claude reads it with Standby's meeting tool. To have it passed as it goes instead, start with `/standby listen live`, or `/standby set transcript live` (Standby's Settings has it too).
4. Press **2** to stop: the summary shows on the band for a while, and stays in Standby. Nothing is sent to Claude, and there's nothing to choose.
5. What to look for: does anything in the terminal move while you listen (only the minutes should); does Claude answer about the meeting when you ask (it should, without you pasting); with Live, do the meeting notes show in the chat as one dim line, and does Claude reply on its own to them (it shouldn't)?

## Settings from Claude Code

`/standby settings` lists the settings that matter here, each with its value and its choices: how a
meeting's transcript reaches Claude (`transcript`: ask or live), what Standby records (`capture`), the
microphone (`mic`), the speech model (`speech_model`), the voice and its model (`voice`,
`voice_model`), talking over the voice (`talk_over`), and a few more. `/standby set <name> <value>`
changes one, for example `/standby set mic AirPods Pro` (`transcript` switches a meeting already
running here too); Standby checks the value and says the
choices when it isn't one of them. Or ask Claude in your own words ("pass the meeting to me as it
goes"): it uses Standby's settings tool. No need to edit `~/.standby/config.json`.

## Calls

A call is a voice conversation with this Claude Code session itself: its quick, spoken side, which
takes real work deeper in the same session. Start one with `/standby call` (or ask Claude to call):

1. Say "what's in the README". The voice answers by itself.
2. Say "run the tests". Your words arrive in Claude Code's chat as a turn of yours, and the voice says it's on it.
3. While it works, say "stop". The turn stops, and the voice says "Stopped it."
4. Ask for something again and wait: when the work is done, the voice tells you how it went, in its own words; the rest is in the chat.
5. On a call the line reads "◉ On a call with Claude 1m (1: mute · 2: end · 3: window)", with the last exchange dim under it; press **2** to end, and the call's summary shows on the band for a while (it stays in Standby).

If `/standby listen` says Standby can't listen here, your terminal app was started from another Claude Code session: start Claude Code with `env -u CLAUDE_CODE_CHILD_SESSION claude`.

For developers: `tools/lite_fake_app.py` serves the same API with a stand-in meeting (no microphone), and `claude plugin test` in this folder runs the mod's tests.
