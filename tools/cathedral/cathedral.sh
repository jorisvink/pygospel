#!/bin/sh
#
# A cathedral in the build directory, for tests.
#
#   ./cathedral.sh up        build a rootless sanctum, start it, provision
#   ./cathedral.sh down      stop it
#   ./cathedral.sh status    is it up, and what has it seen
#   ./cathedral.sh e2e       up, then talk between three chats
#
# The sanctum it runs comes from dist-build/sanctum.sh and everything it
# generates lands in build/cathedral. Ports and keys are throwaway, do not
# point a real flock at this.
#

TOPDIR=`cd "$(dirname "$0")" && pwd`
ROOT=`cd "$TOPDIR/../.." && pwd`

PYTHON=${PYTHON:-python3}
SANCTUM=$ROOT/build/host/sanctum
WORK=$ROOT/build/cathedral
CAT=$WORK/cathedral
CLIENTS=$CAT/clients

FLOCK=493abf95a07e0c00
CSID=5eb2411f
INSTANCE=vigil-python-cathedral
PORT=${PORT:-0}
MTU=1422

die() {
	echo "cathedral: $*" 1>&2
	exit 1
}

# The pids of the guardians of our instance. A sanctum rewrites its argv
# into "instance [role]" and nothing else, so that is what matches here.
guardians() {
	ps -eo pid=,args= | awk -v i="$INSTANCE" \
	    '$0 ~ "^[ ]*[0-9]+ " i " [[]guardian[]]$" { print $1 }'
}

port_taken() {
	ss -lun 2> /dev/null | grep -q " 127.0.0.1:$1 "
}

# 0 means pick one, otherwise keep what we were given.
pick_port() {
	if [ "$PORT" != "0" ]; then
		port_taken $PORT && die "udp port $PORT is taken, PORT=... picks another"
		return
	fi

	for candidate in 14500 14510 14520 14530 14540 14550; do
		if ! port_taken $candidate; then
			PORT=$candidate
			return
		fi
	done

	die "no free port found in the 14500 range"
}

build() {
	(cd $ROOT && ./dist-build/sanctum.sh) || exit 1

	[ -x $SANCTUM/sanctum ] || die "no sanctum built, see build/sanctum-build.log"
}

lay_out() {
	mkdir -p $CAT/identities $CAT/ambries $CAT/clients $WORK/keys

	if [ ! -f $WORK/keys/cs-$CSID ]; then
		echo "generating a cathedral secret and a COSK pair"
		(cd $WORK/keys && $SANCTUM/tools/ambry/ambry cosk-pair \
		    cosk-$CSID cosk-$CSID.pub) || die "cosk-pair failed"
		head -c 32 /dev/urandom > $WORK/keys/cs-$CSID
		chmod 400 $WORK/keys/*
	fi

	# The ambry tool lays a flock out relative to the directory it runs
	# in, so everything it makes and reads lives directly under $WORK.
	if [ ! -d $WORK/$FLOCK/kek-data ]; then
		echo "generating KEKs for flock $FLOCK"
		(cd $WORK && $SANCTUM/tools/ambry/ambry generate $FLOCK) \
		    || die "ambry generate failed"
	fi

	mkdir -p $CAT/identities/flock-$FLOCK

	if [ ! -f $CAT/identities/flock-$FLOCK/$CSID.key ]; then
		cp $WORK/keys/cs-$CSID $CAT/identities/flock-$FLOCK/$CSID.key
		cp $WORK/keys/cosk-$CSID.pub $CAT/identities/flock-$FLOCK/$CSID.pub
		chmod 600 $CAT/identities/flock-$FLOCK/*
	fi

	if [ ! -f $CAT/ambries/ambry-$FLOCK ]; then
		echo "generating the ambry bundle"
		(cd $WORK && $SANCTUM/tools/ambry/ambry bundle $FLOCK $FLOCK 30 \
		    $CAT/ambries/ambry-$FLOCK) || die "ambry bundle failed"
	fi

	{
		echo "flock $FLOCK {"
		for spi in 01 02 03 04 05; do
			echo "    allow $CSID spi 0x$spi 0"
		done
		echo
		echo "    ambry $CAT/ambries/ambry-$FLOCK"
		echo "}"
	} > $CAT/settings.conf

	cat > $CAT/cathedral.conf << EOF
mode cathedral
cathedral_mtu $MTU
instance $INSTANCE

pidfile $CAT/cathedral.pid
control $CAT/control.sock `id -un`

local 127.0.0.1:$PORT
secret $WORK/keys/cs-$CSID
secretdir $CAT/identities
settings $CAT/settings.conf

cathedral_nat_port `expr $PORT + 1`

run control as `id -un`
run cathedral as `id -un`
run purgatory-rx as `id -un`
run purgatory-tx as `id -un`
EOF
}

provision() {
	$PYTHON $TOPDIR/provision.py \
	    --lab "$WORK" --port "$PORT" --mtu 1408 \
	    --flock "$FLOCK" --csid "$CSID" --out "$CLIENTS" --sign 01 02 03
}

down() {
	PIDS=""

	if [ -f $CAT/cathedral.pid ]; then
		PIDS=`cat $CAT/cathedral.pid`
		rm -f $CAT/cathedral.pid
	fi

	for pid in $(guardians); do
		case " $PIDS " in *" $pid "*) continue ;; esac
		PIDS="$PIDS $pid"
	done

	[ -z "$PIDS" ] && return 0

	for pid in $PIDS; do
		kill $pid 2> /dev/null || true
	done

	gone=1

	for _ in 1 2 3 4 5 6 7 8; do
		gone=1

		for pid in $PIDS; do
			kill -0 $pid 2> /dev/null && gone=0
		done

		[ $gone -eq 1 ] && break

		sleep 1
	done

	# A guardian can ignore SIGTERM once its children are gone.
	if [ $gone -eq 0 ]; then
		echo "cathedral: still up after SIGTERM, sending SIGKILL" 1>&2

		for pid in $PIDS; do
			kill -9 $pid 2> /dev/null || true
		done

		sleep 1
	fi
}

up() {
	mkdir -p $WORK
	build
	down
	pick_port
	lay_out

	rm -f $CAT/control.sock

	echo "starting a cathedral on 127.0.0.1:$PORT"

	(cd $WORK && $SANCTUM/sanctum -c $CAT/cathedral.conf \
	    > $CAT/cathedral.log 2>&1 &)

	sleep 2

	if [ ! -f $CAT/cathedral.pid ] ||
	    ! kill -0 `cat $CAT/cathedral.pid` 2> /dev/null; then
		echo "cathedral did not start, log:" 1>&2
		tail -20 $CAT/cathedral.log 1>&2
		exit 1
	fi

	provision

	echo "cathedral up on 127.0.0.1:$PORT, configurations in $CLIENTS"
}

status() {
	if [ -f $CAT/cathedral.pid ] && kill -0 `cat $CAT/cathedral.pid` \
	    2> /dev/null; then
		echo "up on 127.0.0.1:$PORT (pid `cat $CAT/cathedral.pid`)"
	else
		echo "down"
		return 1
	fi

	tail -6 $CAT/cathedral.log 2> /dev/null
}

e2e() {
	if [ ! -f $ROOT/build/host/libkyrka/obj/python/libkyrka.so ]; then
		die "the libkyrka module is not built, run 'make' first"
	fi

	up
	$PYTHON $TOPDIR/e2e.py --seconds ${SECONDS:-60} --group 0007 \
	    $CLIENTS/gospel-01.conf $CLIENTS/gospel-02.conf \
	    $CLIENTS/gospel-03.conf
	ret=$?
	down

	return $ret
}

case "$1" in
up)
	up
	;;
down)
	down
	;;
build)
	build
	;;
provision)
	provision
	;;
status)
	status
	;;
e2e)
	e2e
	;;
*)
	echo "usage: cathedral.sh {up|down|build|provision|status|e2e}"
	exit 1
	;;
esac
