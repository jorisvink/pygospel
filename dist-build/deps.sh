#!/bin/sh

set -e

TOPDIR=`pwd`
SRC=$TOPDIR/build/src

. ./dist-build/fetch.sh

LIBKYRKA_VERSION=1.2.1
LIBKYRKA_URL=https://sanctorum.se/releases/libkyrka-$LIBKYRKA_VERSION.tgz
LIBKYRKA_SHA256=d9450f3b626d34e7f2ebcc7fae8eeb7b1633a67dcf6eb9ebbe0b8c0ce0529fd2

fetch libkyrka $LIBKYRKA_URL $LIBKYRKA_SHA256 \
    libkyrka-$LIBKYRKA_VERSION.tgz $SRC

# libkyrka gets AES-GCM, x25519 and ed25519 from libsodium. Point
# LIBSODIUM_PATH at a built copy if you would rather not use the one
# your distribution installs.
if [ -z "$LIBSODIUM_PATH" ] && ! pkg-config --exists libsodium; then
	echo "no libsodium found, install it or set LIBSODIUM_PATH"
	echo " (pkg-config --cflags libsodium must work)"
	exit 1
fi
