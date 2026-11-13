#!/bin/bash
# Builds Downloads Sorter on this Mac and puts it in Applications.
# Run it in Terminal:  bash install.sh
set -e
here="$(cd "$(dirname "$0")" && pwd)"
support="$HOME/Library/Application Support/DownloadsSorter"

if ! command -v osacompile >/dev/null; then
  echo "This installer has to be run on a Mac." >&2; exit 1
fi

apps="/Applications"
[ -w "$apps" ] || { apps="$HOME/Applications"; mkdir -p "$apps"; }
app="$apps/Downloads Sorter.app"

mkdir -p "$support"
cp "$here/downloads-sorter" "$support/downloads-sorter"
chmod +x "$support/downloads-sorter"

rm -rf "$app" "/Applications/DownloadsSorter.app" 2>/dev/null || true
osacompile -o "$app" "$here/DownloadsSorter.applescript"

echo "Installed: $app"
echo "Opening it now. Click Allow when macOS asks about your Downloads folder."
open "$app"
