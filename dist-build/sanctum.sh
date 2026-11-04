#!/bin/sh

set -e

TOPDIR=`pwd`
BUILD=$TOPDIR/build/host
SRC=$TOPDIR/build/src

SANCTUM_VERSION=${SANCTUM_VERSION:-1.4.2}
SANCTUM_URL=${SANCTUM_URL:-https://sanctorum.se/releases/sanctum-$SANCTUM_VERSION.tgz}
SANCTUM_SHA256=${SANCTUM_SHA256:-886cb491742e0ed788744126b01653dcaa81ea35d502dd8886794fcc5d63c1d5}

. ./dist-build/fetch.sh

fetch sanctum "$SANCTUM_URL" "$SANCTUM_SHA256" \
    sanctum-$SANCTUM_VERSION.tgz $SRC

# Never installed, only tools/cathedral runs it.
OUT=$BUILD/sanctum

# Rebuild when the release or the patch changes.
stamp="$SANCTUM_SHA256 `cat dist-build/rootless.patch | cksum`"

if [ -f $OUT/sanctum ] && [ "`cat $OUT/.build-stamp 2> /dev/null`" = "$stamp" ]; then
	exit 0
fi

echo "building a rootless sanctum $SANCTUM_VERSION for the cathedral"

rm -rf $OUT
mkdir -p $BUILD
cp -R $SRC/sanctum $OUT

if ! patch -s -p1 -d $OUT < dist-build/rootless.patch; then
	echo "dist-build/rootless.patch no longer applies to sanctum $SANCTUM_VERSION" 1>&2
	echo "  (SANCTUM_VERSION=... picks another release)" 1>&2
	rm -rf $OUT
	exit 1
fi

JOBS=`getconf _NPROCESSORS_ONLN 2> /dev/null || echo 4`

make -C $OUT -j$JOBS > $BUILD/sanctum-build.log 2>&1 || {
	echo "sanctum did not build, last lines:" 1>&2
	tail -20 $BUILD/sanctum-build.log 1>&2
	rm -rf $OUT
	exit 1
}

echo "$stamp" > $OUT/.build-stamp
