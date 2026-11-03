# Security

Standby runs on your Mac with access that matters: it records the **microphone**, captures the **sound your Mac
plays** (System Audio Recording), notices your dictation shortcut through **Input Monitoring** and types into other
apps through **Accessibility** (Standby Dictation),
and drives your **Claude Code** sessions, which can read files and run commands. So a security bug in Standby can
be serious, and we'd like to hear about it first.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub: on the repository, **Security > Report
a vulnerability** (a private security advisory). Include what you found, how to reproduce it, and what an
attacker could do with it.

You'll get an answer within a week. We'll agree on a fix and a date before anything is made public, and credit
you in the advisory unless you'd rather not be named.

## What's in scope

- Anything that lets a web page, another app or another user reach Standby's local server (it listens on
  `127.0.0.1` only) and act on it: start or stop recording, read transcripts, or reach a Claude Code session.
- Recording, or sending anything to Claude Code, when the user didn't start it or has paused it.
- Words from a meeting or call ending up somewhere other than `~/.standby` and the user's own Claude Code.
- The installer, or anything Standby downloads, running code that isn't what it says.

Out of scope: what Claude Code itself does with the text it's given, the models' own behaviour, and anything that
needs an attacker who already controls your user account.

## How it's built, for a reviewer

- **The local server** listens on `127.0.0.1` only. Every request needs the token Standby makes fresh at each launch
  (`~/.standby/ui.token`, readable by your user only), compared in constant time; the Host header must be
  `127.0.0.1` or `localhost`, and posts are checked for their Origin, so a web page can't reach it.
- **Your own processes can.** Anything running as your user can read that token, Claude Code's own tools
  included. A prompt injection that gets Claude to run commands could therefore start listening or read a
  transcript through the server, as it could read `~/.standby` directly. Standby doesn't defend against code that
  already runs as you; Claude Code's permission prompts are the guard there.
- **Code signing** is local: the installer makes a signing certificate in its own keychain in `~/.standby/signing`
  (never added to your trust settings, never sent anywhere) and signs both apps with it, so macOS keeps their
  permissions across updates. The apps run Python from `~/.standby/code`, which your user can write to: macOS's
  grants to Standby (microphone, System Audio Recording, Input Monitoring, Accessibility) cover what runs there.
- **The installer** uses no `sudo`, changes no system setting, and checks a sha256 for each file it downloads
  outside Python's and Hugging Face's own tools; packages and models are pinned to a version or a commit.

## Supported versions

Only the latest release, and `main`, get security fixes.
