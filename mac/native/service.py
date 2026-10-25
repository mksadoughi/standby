"""A program the OS starts for the user: at login, and again if it stops by itself (on a
Mac, a LaunchAgent). Standby Dictation is one. When to start it, and what to tell the user,
is the caller's; this only does what it's asked.
"""
import os

from native import impl

_os = impl("service")


def install(name, argv, log_path):
    """`name` starts at login from now on, as argv, its output to log_path. Written only:
    start() runs it now."""
    return _os.install(name, argv, log_path)


def definition_path(name):
    """The file that makes `name` start at login (it may not be there yet): for a helper
    that checks it on its own, without asking this module."""
    return _os.definition_path(name)


def installed(name):
    """Is `name` set to start at login?"""
    return _os.installed(name)


def uninstall(name):
    """`name` no longer starts at login. Stop it first."""
    return _os.uninstall(name)


def loaded(name):
    """Does the OS hold `name` in this login session now (running, or set to run again)?"""
    return _os.loaded(name)


def start(name):
    """(ok, what the OS said): `name` started under the OS, so it runs again if it crashes.
    Already held (a stop still finishing, another start): started again in place."""
    return _os.start(name)


def kickstart(name):
    """(ok, what the OS said): `name`, held by the OS, started again now."""
    return _os.kickstart(name)


def stop(name, wait=5.0):
    """The OS lets go of `name`, waited for up to `wait` seconds, so a start straight
    after is clean. True once it has."""
    return _os.stop(name, wait)


def started_by_service(name, pid=None):
    """Was this process (or `pid`) started by the OS as `name`?"""
    return _os.started_by_service(name, os.getpid() if pid is None else pid)
