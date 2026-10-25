# Security

Standby runs on your Mac with access that matters: it records the **microphone**, captures the **sound your Mac
plays** (Screen & System Audio Recording), types into other apps through **Accessibility** (Standby Dictation),
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

## Supported versions

Only the latest release, and `main`, get security fixes.
