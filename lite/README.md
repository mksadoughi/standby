# Standby

Standby gives your Claude Code session ears and a voice, on your Mac.

- **Call it** and talk things through. It does the real work in your session while you keep talking.
- **Bring it to meetings**, on a call or in the room, with no bot in the call. Ask it anything as it goes.
- **Dictate anywhere.** Press F5, speak, and the text lands where your cursor is.

## It needs the Standby app

This plugin is the Claude Code side of Standby. The listening, the speech recognition and the voice run in the free
Standby app for Mac, so **without the app, this plugin does nothing.** You need:

- an Apple Silicon Mac on macOS 14.4 or later;
- Claude Code 2.1.299 or later, signed in;
- about 6 GB free, for a one-time download of about 4.5 GB (2.5 GB on an 8 GB Mac).

Install the app. Its installer also adds this plugin to Claude Code:

1. Get the code:

   ```bash
   git clone https://github.com/mksadoughi/standby.git
   ```

2. Read the installer, `standby/install.sh`, before you run it. It is a plain shell script in this same repository,
   and it says what it fetches and sets up as it goes (the packages and models listed below).

3. Run it, from the folder you cloned:

   ```bash
   cd standby
   ```

   ```bash
   ./install.sh
   ```

The full guide, with updating and uninstalling, is in the [repository](https://github.com/mksadoughi/standby).
Website and demo film: [mksadoughi.github.io/standby](https://mksadoughi.github.io/standby/)

## Use it

In Claude Code, just ask: "call me" or "listen to this meeting". Or type it yourself:

| | |
|---|---|
| `/standby call` | Start a voice call. Ask for real work and it arrives in the chat, marked "from your call" |
| `/standby listen` | Listen to a meeting. Ask Claude about it in the same chat; the summary shows when it ends |
| `/standby window` | Open the call or meeting in Standby's own window, with names and the live transcript |
| `/standby settings` | Change a setting, or just ask Claude |

While a call or meeting is on, a quiet line above the prompt shows it, with its buttons.

## What it runs and sends

This plugin is a Claude Code mod (`hooks/register.js`, plain readable source, no build step) and a skill for
meetings. It adds the `/standby` command, the line above the prompt and Standby's tools: `listen`, `stop`, `call`,
`end_call`, `tell_voice`, `meeting` and `settings`. Item by item, what it does:

- **The one host it talks to: the Standby app, at `http://127.0.0.1:<port>`, on this Mac.** Every request goes
  through Claude Code's own http call, with the token the app writes for each launch. The mod reads the port and the
  token from `~/.standby/ui.port` and `~/.standby/ui.token`, and nothing else on the disk. It sends no request
  anywhere else. Its live updates are also requests to that address: each waits up to 5 seconds for news.
- **What it sends there:**
  - the actions you take: start, pause or stop a meeting or call, settings you change, names you give speakers;
  - during a call, and only then: what you type in this session, the name of each tool the session runs, and when
    a turn ends, so the voice knows what's going on and can tell you;
  - what Claude tells the voice with the `tell_voice` tool.
- **Programs it starts, each with fixed arguments:**
  - `open -a Standby` (with `-g ... --args --background` to start it without a window): to start the app, or show
    its window when you ask for it;
  - `claude auth status --json`: before a call, to check this Claude Code is signed in;
  - `/bin/sleep 0.5`: a short wait while the app starts, outside the hook's time limit;
  - Standby's agent server, as a call starts, when the app asks: only `agentctl.py ensure`, run by Standby's own
    Python (`~/.standby/venv/bin/python` or `Standby.app`'s), from Standby's code (`~/.standby/code/mac/` or the
    folder beside this plugin). Started from here, macOS counts it as the app this Claude Code runs in, which already
    reads your project. Any other program the app might name is refused.
- **What it puts into the conversation:**
  - a meeting's transcript, labelled `[Standby · meeting]`, with your prompts (or as quiet notes, if you ask for it
    as it goes). Each batch says it's what people said, never instructions to follow;
  - on a call, the work the voice hands to this session, submitted as your prompt in the voice's words, marked "from
    your call", and a short note when a call starts or ends, or one you add after it.
- **Its hooks, and what each decides:**
  - `session.start`: adds the tools and the command, and asks the app every 2 seconds what's on (`/standby off`
    stops this for the session);
  - `command.run` for `/standby`: runs the subcommand you typed;
  - `tool.call` for its own tools (`mcp__standby__*`): it is these tools' answer. A call or listening Claude asks for
    waits for your Accept on the line above the prompt, and only you can turn that asking off;
  - `tool.call` for every tool, during a call only: passes the tool's name to the app, and changes nothing;
  - `tool.call` for `AskUserQuestion`, during a call only: turns the dialog away, telling Claude to ask through
    the voice, since you hear only the voice then. At any other time it does nothing;
  - `classic.PermissionRequest`, during a call only: tells the voice that a permission prompt is waiting, so it can
    say so. It decides nothing: the prompt is yours to answer, as always;
  - `prompt.submit`: while a meeting is heard here, adds its new lines to your prompt; during a call, passes what you
    typed to the app. A single letter typed alone on the terminal's band runs that band key;
  - `turn.start` and `turn.complete`: whether the session is busy, and, during a call, that a turn ended;
  - `ui.render`: draws the line above the prompt, and how the voice's hand-offs read in the chat.
- **No environment it passes on.** The mod reads two environment variables: `HOME`, to find `~/.standby`, and
  `CLAUDE_CODE_CHILD_SESSION`, to know whether this Claude Code saves its conversation. It sends neither anywhere.
  The agent server gets one variable of its own, `STANDBY_AGENT_BY=claude-code`. The one other address in the code,
  `http://www.w3.org/2000/svg`, is only the namespace of Standby's logo drawing, and is never contacted.

The app does the rest, on your Mac:

- **The microphone, and macOS's System Audio Recording** to hear the other side of a video call. macOS asks for each
  the first time it's needed. Dictation asks for Input Monitoring and Accessibility when you turn it on.
- **Audio stays on your Mac.** Speech-to-text, speaker labels and the voice run there.
- **Text goes to your own Claude Code**: what you say on a call, and a meeting's transcript. The app also runs your
  Claude Code for the voice and for each summary. Claude Code talks to its model provider as it always does, on your
  own plan.
- **No account, no API key, no telemetry.** The installer fetches Python packages from PyPI, the models from
  Hugging Face and GitHub, and two display libraries from npm and jsDelivr, each at a fixed version. After that, the
  speech models run offline; only a model you pick later is downloaded.
- **Your meetings and calls** are saved in `~/.standby/sessions/` on your Mac, as transcripts and summaries, until you
  delete them in Standby. No audio is kept, unless you turn on Settings > Meetings > Keep a meeting's audio.
- **Consent**: tell people when you're transcribing. In many places everyone in a conversation has to agree to being
  recorded.

For developers: `tools/lite_fake_app.py` in the repository serves the app's API with a stand-in meeting (no
microphone), and `claude plugin test` in this folder runs the mod's tests.

## License

MIT. See [LICENSE](LICENSE).
