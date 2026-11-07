#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'The macOS app must be built on a macOS runner.' >&2
    exit 1
fi
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
output_dir=${1:-"$PWD/dist/ge-wine-macos"}
mkdir -p "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT
export MACOSX_DEPLOYMENT_TARGET=13.0
python3 -m PyInstaller --noconfirm --clean --distpath "$build_dir/dist" --workpath "$build_dir/work" "$source_dir/ge-wine-macos.spec"
codesign --force --deep --sign - "$build_dir/dist/GE-Wine.app"
codesign --verify --deep --strict "$build_dir/dist/GE-Wine.app"
"$build_dir/dist/GE-Wine.app/Contents/MacOS/GE-Wine" --doctor
architecture=$(uname -m)
name="ge-wine_0.13.0_macos_$architecture"
mkdir -p "$build_dir/image"
cp -R "$build_dir/dist/GE-Wine.app" "$build_dir/image/GE-Wine.app"
ln -s /Applications "$build_dir/image/Applications"
cp "$source_dir/README-macos.md" "$build_dir/image/README.txt"
hdiutil create -quiet -volname GE-Wine -srcfolder "$build_dir/image" -ov -format UDZO "$output_dir/$name.dmg"
ditto -c -k --sequesterRsrc --keepParent "$build_dir/dist/GE-Wine.app" "$output_dir/$name.zip"
(cd "$output_dir" && shasum -a 256 "$name.dmg" "$name.zip" > "$name.sha256")
printf 'macOS build: %s\n' "$output_dir/$name.dmg"
