# One full run at a time per checkout, for tests/run_all.sh (sourced): a second run in
# the same checkout waits for the first. (It used to be one per machine, because five
# sessions' runs overlapping, one suite after another, gave flakes no single run has.
# The suites now run side by side by design, each in a root of its own, so two checkouts
# may run at once; the few that use the display take the machine's lock: STANDBY_REAL_LOCK.) flock isn't on macOS, and mkdir is atomic: the
# lock is a directory holding its owner's pid. A lock whose pid is gone (a run
# killed with -9, or a crash) is stale and taken over; one with no pid yet is
# being taken, unless it has stayed that way for over a minute.
# Runs take it in the order they came: each waits with a ticket ("<seconds>.<pid>")
# in $SUITE_LOCK.queue, and only the oldest live ticket may take the lock. Polling
# alone let a run that came later grab a freed lock first, over and over (one
# run was passed over four times, 2026-10-08). A dead run's ticket is cleared.
# A waiting run can leave the queue by itself, as it was told when it started
# (nobody else need stop it): STANDBY_SUITE_WAIT_MAX=<seconds> gives up after that
# long, and STANDBY_SUITE_IF_HEAD=<commit> once the checkout's HEAD is no longer it
# (rebased or moved on: the run would test code nobody asked for). Either way it
# says so and suite_lock_take returns 3, before any suite runs.
# Single suites never take it. STANDBY_SUITE_LOCK and STANDBY_SUITE_LOCK_POLL
# are for tests/test_suite_lock.sh.
SUITE_LOCK="${STANDBY_SUITE_LOCK:-$(git rev-parse --absolute-git-dir 2>/dev/null || echo /tmp)/standby-suite.lock}"
SUITE_LOCK_POLL="${STANDBY_SUITE_LOCK_POLL:-2}"
SUITE_QUEUE="$SUITE_LOCK.queue"
SUITE_WAIT_MAX="${STANDBY_SUITE_WAIT_MAX:-}"
SUITE_IF_HEAD="${STANDBY_SUITE_IF_HEAD:-}"

suite_lock_owner() { cat "$SUITE_LOCK/pid" 2>/dev/null; }

# Alive, even if it is another user's (kill -0 then says "not permitted").
suite_lock_alive() {
  local why
  why="$(kill -0 "$1" 2>&1)" && return 0
  [[ "$why" == *"not permitted"* ]]
}

# The oldest ticket of a run still alive; a dead run's tickets are cleared on the way.
suite_lock_first() {
  local t
  for t in $(ls "$SUITE_QUEUE" 2>/dev/null | sort -t. -k1,1n -k2,2n); do
    if suite_lock_alive "${t#*.}"; then echo "$t"; return; fi
    rm -f "$SUITE_QUEUE/$t"
  done
}

# Whether a waiting run leaves the queue: it has waited SUITE_WAIT_MAX seconds since
# $1, or the checkout's HEAD is not $2 (SUITE_IF_HEAD, as it resolved when the run began).
suite_lock_give_up() {
  local waited=$(( $(date +%s) - $1 )) head
  if [[ -n "$SUITE_WAIT_MAX" ]] && (( waited >= SUITE_WAIT_MAX )); then
    echo "gave up waiting after $waited s"
    return 0
  fi
  if [[ -n "$SUITE_IF_HEAD" ]]; then
    head="$(git rev-parse -q --verify HEAD 2>/dev/null)"
    if [[ -z "$2" || "$head" != "$2" ]]; then
      echo "the checkout is not at ${SUITE_IF_HEAD} any more (HEAD ${head:0:7}): not running"
      return 0
    fi
  fi
  return 1
}

suite_lock_take() {
  local told="-" owner first ticket since want=""
  since="$(date +%s)"
  [[ -n "$SUITE_IF_HEAD" ]] && want="$(git rev-parse -q --verify "$SUITE_IF_HEAD^{commit}" 2>/dev/null)"
  mkdir -p "$SUITE_QUEUE"
  ticket="$(date +%s).$$"
  : > "$SUITE_QUEUE/$ticket"
  while :; do
    if suite_lock_give_up "$since" "$want"; then
      rm -f "$SUITE_QUEUE/$ticket"
      return 3
    fi
    [[ -e "$SUITE_QUEUE/$ticket" ]] || { mkdir -p "$SUITE_QUEUE"; : > "$SUITE_QUEUE/$ticket"; }   # cleared by hand: queue again
    first="$(suite_lock_first)"
    [[ "$first" == "$ticket" ]] && mkdir "$SUITE_LOCK" 2>/dev/null && break
    owner="$(suite_lock_owner)"
    if [[ ! -d "$SUITE_LOCK" ]]; then        # free, but another run came first
      owner="${first#*.}"
      if [[ "$told" != "$owner" ]]; then
        echo "waiting for another full run (pid $owner)…"
        told="$owner"
      fi
      sleep "$SUITE_LOCK_POLL"
      continue
    fi
    if [[ -n "$owner" ]] && ! suite_lock_alive "$owner"; then
      echo "the last full run (pid $owner) is gone: taking its lock"
      # Only if it is still that one's: another waiter may have taken it meanwhile.
      [[ "$(suite_lock_owner)" == "$owner" ]] && rm -rf "$SUITE_LOCK"
      continue
    fi
    if [[ -z "$owner" && -n "$(find "$SUITE_LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then
      echo "a lock with no run behind it: taking it"
      rm -rf "$SUITE_LOCK"
      continue
    fi
    if [[ "$told" != "$owner" ]]; then
      echo "waiting for another full run (pid ${owner:-?})…"
      told="$owner"
    fi
    sleep "$SUITE_LOCK_POLL"
  done
  echo $$ > "$SUITE_LOCK/pid"
  rm -f "$SUITE_QUEUE/$ticket"
}

# Only our own lock, so a run that lost it (taken over as stale) never frees another's.
suite_lock_release() {
  [[ "$(suite_lock_owner)" == "$$" ]] && rm -rf "$SUITE_LOCK"
  return 0
}
