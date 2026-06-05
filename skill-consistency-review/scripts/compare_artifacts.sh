#!/usr/bin/env bash
# compare_artifacts.sh — light, generic consistency-review helpers.
#
# Portable bash (LF endings; avoid GNU-only flags). If Windows/Git-Bash teammates
# hit friction running this, the documented fallback is to rewrite it in Python3
# (cross-platform, and cleaner for structured extraction such as pulling embedded
# JSON). See SKILL.md.
#
# Usage:
#   compare_artifacts.sh compare FILE1 FILE2 [FILE3 ...]
#       Per-file size + line count; structural extraction (script/svg/th/heading
#       counts); and pairwise diff line counts. HTML is tag-split first so a
#       single-line embedded-data blob does not mask real differences.
#
#   compare_artifacts.sh integrity INSTALLED_DIR SOURCE_DIR
#       Compare every file under SOURCE_DIR against INSTALLED_DIR and report
#       UNCHANGED / MODIFIED / MISSING per file. Use after the runs to confirm
#       no run modified the skill's source.
set -euo pipefail

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

# Split on tag boundaries so HTML one-liners diff meaningfully. Non-HTML passes through.
tag_split() { sed -E 's/></>\
</g' "$1"; }

cmd_compare() {
  if [ "$#" -lt 2 ]; then echo "compare needs >=2 files" >&2; exit 2; fi
  for f in "$@"; do
    if [ ! -f "$f" ]; then echo "no such file: $f" >&2; exit 2; fi
  done

  echo "=== sizes ==="
  for f in "$@"; do
    printf '%s\t%s bytes\t%s lines\n' "$f" "$(wc -c <"$f")" "$(wc -l <"$f")"
  done

  echo "=== structural extraction (occurrence counts) ==="
  # count_occ PATTERN FILE [-E] — number of matches (not lines); pipefail-safe; 0 if none.
  count_occ() {
    local pat="$1" file="$2" flag="${3:-}"
    { grep -o $flag "$pat" "$file" 2>/dev/null || true; } | grep -c '' || true
  }
  for f in "$@"; do
    printf '%s: scripts=%s svg=%s th=%s h1-3=%s\n' "$f" \
      "$(count_occ '<script' "$f")" \
      "$(count_occ '<svg' "$f")" \
      "$(count_occ '<th' "$f")" \
      "$(count_occ '<h[123]' "$f" -E)"
  done

  echo "=== pairwise diff line counts (tag-split) ==="
  local n=$# i j a b dl
  local args=("$@")
  n=${#args[@]}
  for ((i = 0; i < n; i++)); do
    for ((j = i + 1; j < n; j++)); do
      a="${args[$i]}"; b="${args[$j]}"
      dl=$(diff <(tag_split "$a") <(tag_split "$b") | grep -cE '^[<>]' || true)
      echo "$(basename "$a") vs $(basename "$b"): $dl differing lines"
    done
  done
}

cmd_integrity() {
  if [ "$#" -ne 2 ]; then echo "integrity needs INSTALLED_DIR SOURCE_DIR" >&2; exit 2; fi
  local inst="$1" src="$2" rel
  if [ ! -d "$inst" ]; then echo "no such dir: $inst" >&2; exit 2; fi
  if [ ! -d "$src" ]; then echo "no such dir: $src" >&2; exit 2; fi
  ( cd "$src" && find . -type f ) | while read -r rel; do
    if [ ! -f "$inst/$rel" ]; then
      echo "MISSING:   ${rel#./}"
    elif cmp -s "$src/$rel" "$inst/$rel"; then
      echo "UNCHANGED: ${rel#./}"
    else
      echo "MODIFIED:  ${rel#./}"
    fi
  done
}

case "${1:-}" in
  compare) shift; cmd_compare "$@" ;;
  integrity) shift; cmd_integrity "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "unknown subcommand: $1" >&2; usage; exit 2 ;;
esac
