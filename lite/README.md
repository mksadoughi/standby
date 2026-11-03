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
- about 5 GB free, for a one-time download of about 4.5 GB (2.5 GB on an 8 GB Mac).

Install the app. The installer also adds this plugin to Claude Code:

```bash
git clone https://github.com/mksadoughi/standby.git
cd standby && ./install.sh
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

- **This plugin** is a Claude Code mod (`hooks/register.js`) and a skill for meetings. It adds the `/standby`
  commands, the line above the prompt and Standby's tools. It talks only to the Standby app on this Mac, at
  127.0.0.1, with a token the app writes in `~/.standby`.
- **The app** uses your microphone, and macOS's System Audio Recording to hear the other side of a video call. macOS
  asks for each the first time it's needed. Dictation asks for Input Monitoring and Accessibility when you turn it on.
- **Audio stays on your Mac.** Speech-to-text, speaker labels and the voice run there.
- **Text goes to your own Claude Code**: what you say on a call, and a meeting's transcript. Claude Code talks to its
  model provider as it always does, on your own plan.
- **No account, no API key, no telemetry.** The installer fetches Python packages and the models once, from PyPI, npm,
  Hugging Face and GitHub. After that, the speech models run offline.
- **Your meetings and calls** are saved in `~/.standby/sessions/` on your Mac.
- **Consent**: tell people when you're transcribing. In many places everyone in a conversation has to agree to being
  recorded.

For developers: `tools/lite_fake_app.py` in the repository serves the app's API with a stand-in meeting (no
microphone), and `claude plugin test` in this folder runs the mod's tests.

## License

MIT. See [LICENSE](LICENSE).
