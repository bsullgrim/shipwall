#!/usr/bin/env bash
# backup.sh -- snapshot the irreplaceable Ship Wall data files to a separate
# private git repo (off-card, off-site, versioned).
#
# WHY THIS EXISTS: the hand-curated logs (passages.csv, register.csv, the MMSI
# database) live on the Pi's SD card -- the single point of failure. They are
# deliberately gitignored in the CODE repo (the live service rewrites them, which
# would cause endless merge churn), so they get their OWN backup repo here. This
# copies them out and pushes on a schedule, so an SD-card failure costs you at
# most the time since the last run, not years of history.
#
# DESIGN FOR A DYING CARD: reads only the small data files and commits ONLY when
# content changed (git detects this) -- so on a quiet night it does almost no
# writes. It never rewrites the source files and never runs heavy IO.
#
# SETUP (one time):
#   1. Create a PRIVATE repo on GitHub, e.g. bsullgrim/shipwall-data
#   2. On the Pi:
#        mkdir -p /home/grims/shipwall-data && cd /home/grims/shipwall-data
#        git init && git branch -M main
#        git remote add origin git@github.com:bsullgrim/shipwall-data.git
#        # (use an SSH deploy key or a PAT so pushes are non-interactive)
#   3. Point BACKUP_REPO below at that directory.
#
# Run manually to test:  ./backup.sh
# Then install the timer (shipwall-backup.service / .timer) to run it nightly.

set -euo pipefail

# --- config (override via environment / the systemd unit) --------------------
SRC_DIR="${SRC_DIR:-/home/grims/shipwall}"
BACKUP_REPO="${BACKUP_REPO:-/home/grims/shipwall-data}"
BRANCH="${BACKUP_BRANCH:-main}"
# Push to the remote after committing. Set BACKUP_PUSH=0 to commit locally only
# (e.g. if the network/credentials aren't set up yet -- local commits still give
# you versioned history on a *different* part of the card, and you can push later).
PUSH="${BACKUP_PUSH:-1}"

# The irreplaceable, hand-curated / accumulated data files. Add any others here.
# Missing files are skipped quietly (not every deployment has every file).
FILES=(
    passages.csv
    register.csv
    mmsi_database.json
    fun_stats.json
    unknown_vessels.json
    ship_to_operator.json
)

log() { echo "[backup $(date -Iseconds)] $*"; }

# --- preflight ---------------------------------------------------------------
if [ ! -d "$BACKUP_REPO/.git" ]; then
    log "ERROR: $BACKUP_REPO is not a git repo. See SETUP in this script's header."
    exit 1
fi

# git needs an identity to commit. Under a systemd unit there's no interactive
# git config, and a missing identity otherwise fails deep inside `git commit`
# with a confusing error. Check up front and say exactly how to fix it.
if ! git -C "$BACKUP_REPO" config user.email >/dev/null 2>&1 \
   && ! git config --global user.email >/dev/null 2>&1; then
    log "ERROR: no git identity set. Configure it once:"
    log "  git -C $BACKUP_REPO config user.email 'you@example.com'"
    log "  git -C $BACKUP_REPO config user.name  'Your Name'"
    exit 1
fi

# --- copy each data file into the backup repo, if it reads cleanly ------------
# cp can fail if the source is mid-write or (relevant here) corrupted on a bad
# card. A failed copy must NOT abort the whole run -- back up whatever is good,
# and loudly flag whatever isn't. We copy to a temp name first and CHECK IT FOR
# CORRUPTION before replacing the repo copy, so neither a failed/partial read nor
# a readable-but-corrupt file (null bytes -- the exact failure that hit
# register.csv) can overwrite a previously-good backup with garbage.
copied=0
failed=()

# True if the file contains NUL bytes (i.e. is corrupt). CSV/JSON data files are
# text and must never contain a NUL; one means torn/corrupted content.
has_nulls() {
    [ "$(python3 - "$1" <<'PY'
import sys
print("NULL" if b"\x00" in open(sys.argv[1], "rb").read() else "OK")
PY
)" = "NULL" ]
}

for f in "${FILES[@]}"; do
    src="$SRC_DIR/$f"
    [ -e "$src" ] || { log "skip (absent): $f"; continue; }
    dst="$BACKUP_REPO/$f"
    tmp="$dst.tmp.$$"
    if ! cp -- "$src" "$tmp" 2>/dev/null; then
        rm -f -- "$tmp" 2>/dev/null || true
        failed+=("$f")
        log "WARNING: could not read $f (mid-write?) -- kept previous backup"
        continue
    fi
    # Refuse to commit a corrupt snapshot: if the copy has NUL bytes, discard it
    # and KEEP the last known-good version already in the repo. This is the whole
    # point -- a backup taken after corruption must not replace a clean one.
    if has_nulls "$tmp"; then
        rm -f -- "$tmp" 2>/dev/null || true
        failed+=("$f")
        log "WARNING: $f is CORRUPT (null bytes) -- NOT backing up, kept previous good copy"
        continue
    fi
    mv -- "$tmp" "$dst"
    copied=$((copied + 1))
done

if [ "${#failed[@]}" -gt 0 ]; then
    log "!! ${#failed[@]} file(s) skipped (unreadable or corrupt): ${failed[*]}"
    # On a healthy system this never happens; on a failing card / after a bad
    # power-cut it's the early warning. We still commit the files that were clean.
fi

# --- commit only if something changed ----------------------------------------
cd "$BACKUP_REPO"
git add -A
if git diff --cached --quiet; then
    log "no changes since last backup ($copied file(s) checked); nothing to commit"
    exit 0
fi

# A short, dated commit. Include a one-line summary of what changed.
summary="$(git diff --cached --stat | tail -1 | sed 's/^ *//')"
msg="backup $(date -Iseconds) -- ${summary:-update}"
if [ "${#failed[@]}" -gt 0 ]; then
    msg="$msg [WARN: ${#failed[@]} file(s) skipped unreadable/corrupt: ${failed[*]}]"
fi
git commit -q -m "$msg"
log "committed: $msg"

# --- push (best-effort, self-healing on divergence) --------------------------
if [ "$PUSH" = "1" ]; then
    # First push attempt.
    if git push -q origin "$BRANCH" 2>/dev/null; then
        log "pushed to origin/$BRANCH"
    else
        # A push can be rejected simply because the remote has commits we don't
        # (e.g. an edit made on the GitHub web UI, or a push from another machine).
        # Without handling this, every subsequent run would keep failing and quietly
        # pile up unpushed commits. Try to reconcile by rebasing our backup commits
        # on top of the remote, then push again. This is safe here because this repo
        # is a machine-generated backup destination -- our local commits are the
        # authoritative data snapshots and just need to sit on top of whatever else
        # landed remotely.
        log "push rejected; attempting pull --rebase to reconcile divergence"
        if git pull --rebase -q origin "$BRANCH" 2>/dev/null \
           && git push -q origin "$BRANCH" 2>/dev/null; then
            log "reconciled and pushed to origin/$BRANCH"
        else
            log "WARNING: push still failing after rebase (network down, auth, or a rebase conflict)."
            log "         Commit(s) are saved locally and will retry next run. If this persists,"
            log "         check: cd $BACKUP_REPO && git status && git push origin $BRANCH"
        fi
    fi
fi

log "done ($copied file(s) up to date)."