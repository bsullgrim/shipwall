#!/usr/bin/env bash
# restore.sh -- rebuild the Ship Wall data layer onto a FRESH card.
#
# Run this AFTER: flashing a new high-endurance SD card, and `git clone`-ing the
# CODE repo (github.com/bsullgrim/shipwall) to ~/shipwall. This drops the
# irreplaceable data files (which are gitignored in the code repo, so a clone
# doesn't include them) back into place from your backup folder, verifying each
# is clean before installing it -- so a corrupted backup copy can't silently
# reintroduce garbage.
#
# It does NOT: recreate .env (secrets), rebuild the venv, or install systemd
# units with sudo -- those are printed as explicit next steps at the end.
#
# Usage:
#   ./restore.sh /path/to/backup-folder            # restore into ~/shipwall
#   ./restore.sh /path/to/backup-folder ~/shipwall # explicit destination
#   DRY_RUN=1 ./restore.sh /path/to/backup-folder  # show what it would do
#   FORCE=1   ./restore.sh /path/to/backup-folder  # overwrite existing files
#
# The backup folder is wherever you keep the confirmed-clean copies -- e.g. the
# shipwall-data repo (git clone that too), or the folder you scp'd to your
# laptop. Point SRC at it.

set -euo pipefail

SRC="${1:-}"
DEST="${2:-$HOME/shipwall}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"

if [ -z "$SRC" ]; then
    echo "usage: $0 <backup-folder> [dest=~/shipwall]"
    echo "  the backup folder holds your clean data files (shipwall-data clone, or your scp'd copies)"
    exit 2
fi
[ -d "$SRC" ]  || { echo "ERROR: backup folder not found: $SRC"; exit 1; }
[ -d "$DEST" ] || { echo "ERROR: destination not found: $DEST (git clone the code repo there first)"; exit 1; }

# The runtime data files that live outside git. mmsi_to_operator.json is
# deliberately NOT here -- it was the corrupted/derived file; it's rebuilt from
# the others at runtime if anything still needs it.
DATA_FILES=(
    passages.csv
    register.csv
    mmsi_database.json
    ship_to_operator.json
    unknown_vessels.json
    fun_stats.json
    emmett_panel_data.json
    config.json
    LIVERY_PALETTES.json
    CANON_MASK_SPANS.json
    mmsi_db.json
)

# Sprite assets that live in the Arduino sketch dir (ship_sprites.h) are in the
# code repo already, so the clone brings them -- not restored here.

# Returns success (0) when the file contains NUL bytes (i.e. is corrupt).
# Kept explicit so the sense can't be misread: prints "NULL" iff a NUL is found,
# and we test that string -- no reliance on clever exit-code inversion.
has_nulls() {
    [ "$(python3 - "$1" <<'PY'
import sys
data = open(sys.argv[1], "rb").read()
print("NULL" if b"\x00" in data else "OK")
PY
)" = "NULL" ]
}

echo "==> restore.sh"
echo "    from: $SRC"
echo "    into: $DEST"
[ "$DRY_RUN" = 1 ] && echo "    (DRY RUN -- no files will be written)"
echo

restored=0; skipped=0; missing=0; corrupt=0
for f in "${DATA_FILES[@]}"; do
    src="$SRC/$f"
    dst="$DEST/$f"
    if [ ! -e "$src" ]; then
        echo "  -- missing in backup:  $f"
        missing=$((missing+1))
        continue
    fi
    # Guard: never restore a corrupted backup copy over anything.
    if has_nulls "$src"; then
        echo "  !! CORRUPT in backup:  $f  (has null bytes -- NOT restoring)"
        corrupt=$((corrupt+1))
        continue
    fi
    # Don't clobber an existing file unless forced (protects live data on re-run).
    if [ -e "$dst" ] && [ "$FORCE" != 1 ]; then
        echo "  == exists, skipping:   $f  (use FORCE=1 to overwrite)"
        skipped=$((skipped+1))
        continue
    fi
    if [ "$DRY_RUN" = 1 ]; then
        echo "  ++ would restore:      $f"
    else
        cp -- "$src" "$dst"
        echo "  ++ restored:           $f"
    fi
    restored=$((restored+1))
done

echo
echo "==> data: $restored restored, $skipped skipped, $missing missing, $corrupt corrupt-in-backup"
if [ "$corrupt" -gt 0 ]; then
    echo "!! $corrupt backup file(s) had null bytes -- find a clean copy before trusting them."
fi

# --- remaining manual steps (need secrets / sudo / network) ------------------
cat <<EOF

==> Data layer done. Remaining steps to a running wall:

  1. Recreate .env (your AISSTREAM_KEY etc.) -- NOT in any repo:
       nano $DEST/.env
       # AISSTREAM_KEY=...

  2. Rebuild the Python venv:
       cd $DEST
       python3 -m venv .venv
       .venv/bin/pip install -r requirements.txt   # or: aiohttp websockets pyserial ...

  3. Install + enable the systemd units (from your backup or the repo):
       sudo cp $DEST/systemd/*.service $DEST/systemd/*.timer /etc/systemd/system/ 2>/dev/null \\
         || echo "   (units not in repo -- copy them from your backup folder)"
       sudo systemctl daemon-reload
       sudo systemctl enable --now shipwall.service shipwall-web.service
       sudo systemctl enable --now shipwall-backup.timer   # so backups run from day one

  4. Set the mirror + confirm:
       # MIRROR_HOST drop-in on shipwall.service:
       sudo systemctl edit shipwall.service    # add: [Service]\\nEnvironment=MIRROR_HOST=localhost:8080
       sudo systemctl daemon-reload && sudo systemctl restart shipwall.service
       curl -s localhost:8080/latest           # ts should go non-zero within ~10s

  5. Re-expose publicly:
       sudo tailscale funnel --bg 8080

EOF
echo "==> restore complete."
