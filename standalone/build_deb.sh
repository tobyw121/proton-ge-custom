#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build without root, Docker, or modifications to Proton/Wine sources.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
output_dir=${1:-/workspace/ge-wine-dist}
mkdir -p "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
build_dir=$(mktemp -d)
trap 'rm -rf -- "$build_dir"' EXIT
umu_commit=e2b203a1fdd2af9f35166f5713cb3f85d72587e2
git init --quiet "$build_dir/umu-source"
git -C "$build_dir/umu-source" fetch --quiet --depth 1 \
    https://github.com/Open-Wine-Components/umu-launcher.git "$umu_commit"
git -C "$build_dir/umu-source" checkout --quiet --detach FETCH_HEAD
test "$(git -C "$build_dir/umu-source" rev-parse HEAD)" = "$umu_commit"
stage="$build_dir/package"
install -Dm644 "$source_dir/ge_wine.py" "$stage/usr/lib/ge-wine/ge_wine.py"
mkdir -p "$stage/usr/lib/ge-wine/backend" "$stage/DEBIAN" \
    "$stage/usr/share/doc/ge-wine" "$stage/usr/share/applications"
cp -R "$build_dir/umu-source/umu" "$stage/usr/lib/ge-wine/backend/"
rm -rf "$stage/usr/lib/ge-wine/backend/umu/umu-launcher"
find "$stage/usr/lib/ge-wine/backend" -type d -exec chmod 755 {} +
find "$stage/usr/lib/ge-wine/backend" -type f -exec chmod 644 {} +
install -Dm644 "$build_dir/umu-source/LICENSE" "$stage/usr/share/doc/ge-wine/UMU-LICENSE"
install -Dm644 "$source_dir/README.md" "$stage/usr/share/doc/ge-wine/README.md"
install -Dm644 "$source_dir/build_deb.sh" "$stage/usr/share/doc/ge-wine/build_deb.sh"
# Retain corresponding UMU source, build files and license for redistribution.
git -C "$build_dir/umu-source" archive --format=tar HEAD | gzip -n > \
    "$stage/usr/share/doc/ge-wine/umu-source.tar.gz"
printf '%s\n' "$umu_commit" > "$stage/usr/share/doc/ge-wine/umu-commit"
mkdir -p "$stage/usr/bin"
cat > "$stage/usr/bin/ge-wine" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/lib/ge-wine/ge_wine.py "$@"
EOF
chmod 755 "$stage/usr/bin/ge-wine"
cat > "$stage/usr/share/applications/ge-wine.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=GE-Wine
Comment=Windows-Programme mit GE-Proton starten
Exec=ge-wine --gui %f
TryExec=ge-wine
Icon=application-x-executable
Terminal=false
Categories=Utility;Game;
MimeType=application/x-ms-dos-executable;application/vnd.microsoft.portable-executable;
StartupNotify=false
EOF
cat > "$stage/DEBIAN/control" <<'EOF'
Package: ge-wine
Version: 0.1.0
Architecture: amd64
Maintainer: GE-Wine contributors <ge-wine@example.invalid>
Section: utils
Priority: optional
Depends: python3 (>= 3.12), python3-tk, python3-xlib, python3-urllib3 (>= 2.0), python3-truststore, ca-certificates, xz-utils, tar, libvulkan1
Description: Desktop and command-line frontend for GE-Proton through UMU
 Includes a private UMU backend. GE-Proton and the Steam Linux Runtime
 are downloaded on first launch. A separate Wine installation is not needed.
EOF
find "$stage" -type d -exec chmod 755 {} +
find "$stage" -type f -exec chmod 644 {} +
chmod 755 "$stage/usr/bin/ge-wine"
dpkg-deb --root-owner-group --build "$stage" "$output_dir/ge-wine_0.1.0_amd64.deb"
(cd "$output_dir" && sha256sum ge-wine_0.1.0_amd64.deb > ge-wine_0.1.0_amd64.deb.sha256)
printf 'Package: %s/ge-wine_0.1.0_amd64.deb\n' "$output_dir"
