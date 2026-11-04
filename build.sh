#!/bin/sh
# Build dt295-agent:local from a pix-userland checkout.
#
#   ./build.sh [--userland DIR] [--assets DIR] [--tag TAG]
#
# pix-ts-legacy is rebuilt with -DDT295_CAPTURE_TESTING (the default build is a
# stub on Linux), and the DT295 bundle is copied next to it. Neither is tracked
# here: both are build inputs, not sources.
set -eu

userland=${USERLAND:-"$HOME/pix-userland"}
assets=${ASSETS:-}
tag=${TAG:-dt295-agent:local}

while [ $# -gt 0 ]; do
    case "$1" in
        --userland) userland=$2; shift 2 ;;
        --assets) assets=$2; shift 2 ;;
        --tag) tag=$2; shift 2 ;;
        -h|--help)
            sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

root=$(cd "$(dirname "$0")" && pwd)
[ -f "$userland/Makefile" ] || {
    echo "no pix-userland checkout at $userland" >&2
    exit 1
}

echo "building pix-ts-legacy (recompiles every object)"
make -C "$userland" -B -j"$(nproc 2>/dev/null || echo 2)" pix-ts-legacy \
    CFLAGS="-O2 -g -DDT295_CAPTURE_TESTING"
cp "$userland/pix-ts-legacy" "$root/pix-ts-legacy"

if [ -z "$assets" ]; then
    if [ -d "$userland/assets" ]; then
        assets="$userland/assets"
    else
        assets="$userland/firmware"
    fi
fi
[ -d "$assets" ] || { echo "no assets directory: $assets" >&2; exit 1; }

rm -rf "$root/assets"
mkdir -p "$root/assets"
cp -a "$assets"/. "$root/assets"/
# The standalone CLI reads <assets>/dt295.assets; the upstream tree ships the
# same bundle as firmware/dt295.bin.
if [ ! -f "$root/assets/dt295.assets" ] && [ -f "$root/assets/dt295.bin" ]; then
    cp "$root/assets/dt295.bin" "$root/assets/dt295.assets"
fi

docker build -t "$tag" "$root"
echo "built $tag"
