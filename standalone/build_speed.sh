#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
output_dir=${1:-"$source_dir/speed"}
for architecture in 64 32; do
    mkdir -p "$output_dir/lib$architecture"
    gcc -m"$architecture" -std=gnu11 -O2 -Wall -Wextra -Werror -fPIC -shared \
        -Wl,-z,relro,-z,now -o "$output_dir/lib$architecture/libge_speed.so" \
        "$source_dir/speed/ge_speed.c" "$source_dir/speed/ge_attach.c" -ldl -pthread
done
