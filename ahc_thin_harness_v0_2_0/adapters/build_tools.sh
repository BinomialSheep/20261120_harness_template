#!/usr/bin/env bash
# Build the official Rust tools and put gen / vis / tester into problem/bin/.
#   bash problem/build_tools.sh            (run from the project root)
# Cargo builds in .build/tools-target, outside problem/, because problem/ is copied into every round and workspace.
set -euo pipefail

root="$(pwd)"
tools="$root/problem/tools"
target="$root/.build/tools-target"
[ -f "$tools/Cargo.toml" ] || { echo "problem/tools/Cargo.toml が無い（公式ツールを problem/tools/ に置く）" >&2; exit 1; }
command -v cargo >/dev/null || { echo "cargo が無い（https://www.rust-lang.org/ から Rust を入れる）" >&2; exit 1; }

CARGO_TARGET_DIR="$target" cargo build --release --bins --manifest-path "$tools/Cargo.toml"
mkdir -p "$root/problem/bin"
for name in gen vis tester; do
  if [ -x "$target/release/$name" ]; then install -m 755 "$target/release/$name" "$root/problem/bin/$name"; fi
done

# gen must reproduce the distributed input of seed 0.
if [ -x "$root/problem/bin/gen" ] && [ -f "$tools/in/0000.txt" ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  (cd "$tmp" && printf '0\n' > seeds.txt && "$root/problem/bin/gen" seeds.txt >/dev/null)
  cmp -s "$tmp/in/0000.txt" "$tools/in/0000.txt" || { echo "gen が in/0000.txt を再現しない" >&2; exit 1; }
fi
ls "$root/problem/bin"
