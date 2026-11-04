#!/bin/sh

set -e

TOPDIR=`pwd`
LIBKYRKA=${LIBKYRKA:-$TOPDIR/build/src/libkyrka}
BUILD=$TOPDIR/build/host
SRC=$BUILD/libkyrka

if [ ! -f $LIBKYRKA/include/libkyrka/libkyrka.h ]; then
	echo "no libkyrka found at $LIBKYRKA, run dist-build/deps.sh"
	exit 1
fi

# Rebuild when the tarball or the patches change.
stamptext=`cat $LIBKYRKA/.vigil-python-fetched 2> /dev/null`
stamptext="$stamptext `cat dist-build/patches/libkyrka-*.patch | cksum`"

if [ -f $SRC/obj/python/libkyrka.so ] &&
    [ "`cat $SRC/.build-stamp 2> /dev/null`" = "$stamptext" ]; then
	exit 0
fi

mkdir -p $BUILD
rm -rf $SRC
cp -R $LIBKYRKA $SRC
rm -rf $SRC/.git $SRC/.vigil-python-fetched

for p in dist-build/patches/libkyrka-*.patch; do
	[ -f "$p" ] || continue
	echo "patching libkyrka with `basename $p`"
	patch -s -p1 -d $SRC < $p
done

if [ -n "$LIBSODIUM_PATH" ]; then
	EXTRA="LIBSODIUM_PATH=$LIBSODIUM_PATH"
fi

make -C $SRC python-mod $EXTRA

echo "$stamptext" > $SRC/.build-stamp
