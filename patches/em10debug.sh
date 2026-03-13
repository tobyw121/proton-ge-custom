#!/bin/bash

# patch functions
apply_patch() {
    local patch_path="$1"
    patch -Np1 < "$patch_path"
}

apply_all_in_dir() {
    local dir="$1"
    for patch in "$dir"/*.patch; do
        echo "$patch"
        apply_patch "$patch"
    done
}

### (1) PREP SECTION ###

    pushd wine
    git reset --hard HEAD
    git clean -xdf


    echo "WINE: -CUSTOM- ETASSH WINE-WAYLAND+ PATCHES"
    apply_all_in_dir "../patches/wine-hotfixes/wine-wayland/"

    popd



### END PROTON-GE ADDITIONAL CUSTOM PATCHES ###
### END WINE PATCHING ###
