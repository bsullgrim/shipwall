#!/usr/bin/env bash
# deploy.sh -- pull latest shipwall and restart the service, guarded.
#
# Run on the Pi from the repo root. Pulls origin/main, validates that the
# Python still compiles BEFORE touching the running service, then restarts.
# Aborts and leaves the service untouched if anything looks wrong, so a bad
# push can never take the wall down -- the old code keeps running.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/grims/shipwall}"
SERVICE="${SERVICE:-shipwall shipwall-web}"
BRANCH="${BRANCH:-main}"
PYTHON="${PYTHON:-$REPO_DIR/.venv/bin/python3}"

cd "$REPO_DIR"

echo "==> deploy.sh: $REPO_DIR  (service=$SERVICE branch=$BRANCH)"

# 1. Stash any local edits so the pull can't fail on a dirty tree. Runtime data
#    files are gitignored, so this only catches accidental source edits made on
#    the Pi -- exactly the divergence we want to surface, not silently clobber.
STASHED=0
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "==> uncommitted changes present; stashing"
    git stash push -u -m "deploy.sh autostash $(date -Iseconds)"
    STASHED=1
fi

# 2. Record where we are, so we can roll back if validation fails.
OLD_REV="$(git rev-parse HEAD)"

# 3. Fetch + fast-forward to origin/BRANCH. Fast-forward only: if the Pi's main
#    has diverged (someone committed on the Pi), this FAILS rather than making a
#    merge commit -- you want to know about divergence, not paper over it.
echo "==> fetching origin/$BRANCH"
git fetch origin "$BRANCH"
NEW_REV="$(git rev-parse "origin/$BRANCH")"

if [ "$OLD_REV" = "$NEW_REV" ]; then
    echo "==> already up to date at ${OLD_REV:0:8}; nothing to deploy"
    [ "$STASHED" = 1 ] && echo "    (note: stashed local changes remain stashed)"
    exit 0
fi

echo "==> $OLD_REV -> $NEW_REV"
git merge --ff-only "origin/$BRANCH"

# 4. VALIDATE before restarting. Compile every tracked .py; if any fails, roll
#    the working tree back to OLD_REV and leave the running service alone.
echo "==> compiling Python (validation gate)"
if ! git ls-files '*.py' -z | xargs -0 "$PYTHON" -m py_compile; then
    echo "!! compile FAILED -- rolling back to ${OLD_REV:0:8}, service untouched"
    git reset --hard "$OLD_REV"
    exit 1
fi
echo "==> compile OK"

# 5. Restart the services and confirm each actually came up. SERVICE may name
#    more than one unit (e.g. the AIS service + the web app) -- restart them
#    together, then verify each independently so a dead web app can't hide
#    behind a healthy ingest service (the failure that kept serving stale code).
echo "==> restarting: $SERVICE"
# shellcheck disable=SC2086  -- intentional word-splitting: $SERVICE is a unit list
sudo systemctl restart $SERVICE
sleep 3
FAILED=""
for unit in $SERVICE; do
    if systemctl is-active --quiet "$unit"; then
        echo "==> $unit active."
    else
        echo "!! $unit did NOT come up. Recent log:"
        journalctl -u "$unit" -n 30 --no-pager
        FAILED="$FAILED $unit"
    fi
done
if [ -n "$FAILED" ]; then
    echo "!! Code is at $NEW_REV but these units are down:$FAILED"
    echo "!! Investigate before assuming the wall is live."
    exit 1
fi
echo "==> all units active. Deploy complete at ${NEW_REV:0:8}."
echo "    tail logs:  journalctl -u ${SERVICE%% *} -f"