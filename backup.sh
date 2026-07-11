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
# and loudly flag whatever isn't. We copy to a temp name first and only replace
# the repo copy on success, so a failed/partial read never overwrites a
# previously-good backup with garbage.
copied=0
failed=()
for f in "${FILES[@]}"; do
    src="$SRC_DIR/$f"
    [ -e "$src" ] || { log "skip (absent): $f"; continue; }
    dst="$BACKUP_REPO/$f"
    tmp="$dst.tmp.$$"
    if cp -- "$src" "$tmp" 2>/dev/null; then
        mv -- "$tmp" "$dst"
        copied=$((copied + 1))
    else
        rm -f -- "$tmp" 2>/dev/null || true
        failed+=("$f")
        log "WARNING: could not read $f (mid-write or corrupted?) -- kept previous backup"
    fi
done

if [ "${#failed[@]}" -gt 0 ]; then
    log "!! ${#failed[@]} file(s) failed to copy: ${failed[*]}"
    # On a healthy system this never happens; on a failing card it's the early
    # warning. We still commit the files that DID copy.
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
    msg="$msg [WARN: ${#failed[@]} file(s) unreadable: ${failed[*]}]"
fi
git commit -q -m "$msg"
log "committed: $msg"

# --- push (best-effort) ------------------------------------------------------
if [ "$PUSH" = "1" ]; then
    if git push -q origin "$BRANCH" 2>/dev/null; then
        log "pushed to origin/$BRANCH"
    else
        log "WARNING: push failed (network/credentials?). Commit is saved locally; will push next run."
        # Not fatal: the local commit is safe, and the next successful run pushes
        # this plus the new one. Don't exit non-zero just for a transient push
        # failure, or the timer will report the unit as failed on every offline run.
    fi
fi

log "done ($copied file(s) up to date)."
