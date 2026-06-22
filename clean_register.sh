#!/usr/bin/env bash
# clean_register.sh -- atomically compact register.csv, keeping a dated backup.
# Called by shipwall-clean.service (monthly). Never destroys the original on
# failure: clean to a temp, sanity-check it, swap in, keep one backup.
set -euo pipefail

DIR="/home/grims/shipwall"
REG="$DIR/register.csv"
TMP="$DIR/register.clean.$$.csv"
STAMP="$(date +%Y%m)"
BAK="$DIR/register.bak.$STAMP.csv"

cd "$DIR"

# Nothing to do if the file doesn't exist yet.
[ -f "$REG" ] || { echo "no register.csv yet; skipping"; exit 0; }

orig_lines=$(wc -l < "$REG")

# Compact. clean_register.py re-resolves operators through current rules and
# drops signature-flicker duplicates.
python3 clean_register.py "$REG" -o "$TMP"

# Sanity gate: the cleaned file must exist, be non-trivial (header + >=1 row),
# and not be larger than the original (compaction should shrink or hold).
new_lines=$(wc -l < "$TMP")
if [ ! -s "$TMP" ] || [ "$new_lines" -lt 2 ]; then
  echo "clean produced an empty/header-only file ($new_lines lines); aborting, original kept"
  rm -f "$TMP"
  exit 1
fi
if [ "$new_lines" -gt "$orig_lines" ]; then
  echo "clean grew the file ($orig_lines -> $new_lines); suspicious, aborting"
  rm -f "$TMP"
  exit 1
fi

# Keep one dated backup, then atomically replace. Briefly stop the service so no
# row is appended between clean_register.py's read and the swap (such a row would
# survive only in the backup, not the live file). The panel keeps showing its
# last frame during the ~2s gap; AISStream backfills on reconnect.
cp -f "$REG" "$BAK"
systemctl stop shipwall.service || true
mv -f "$TMP" "$REG"          # mv within same fs is atomic
# Script runs as root; restore ownership so the (non-root) service can append.
chown grims:grims "$REG" "$BAK" 2>/dev/null || true
systemctl start shipwall.service || true
echo "compacted register.csv: $orig_lines -> $new_lines lines (backup: $BAK)"

# Retain only the 3 most recent monthly backups.
ls -1t "$DIR"/register.bak.*.csv 2>/dev/null | tail -n +4 | xargs -r rm -f

# Note: the running service holds its own in-memory _logged dedup, so it keeps
# appending to the now-compacted file without missing a beat. The next service
# RESTART re-seeds _logged from the compacted file -- which is the point.
