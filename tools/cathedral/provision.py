#!/usr/bin/env python3

"""
Write gospel configurations for devices in a test flock.

Run it through cathedral.sh, which passes the flock, the cathedral identity
and where the keys live:

    $ ./cathedral.sh provision

Each device gets its KEK out of the ambry tool's kek-data, the cathedral
secret and COSK that the cathedral expects, and with --sign an instance
name signed the way Covenant signs one. That last bit needs the ed25519
from `cryptography`, which is also what pygospel.py checks names with.
"""

import os
import sys
import struct
import argparse

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
except ImportError:
    sys.exit("provision.py needs 'cryptography' for signed names, "
        "run it without --sign if you do not want them")

NAME_LABEL = b"COVENANT.NAME"
NAME_MSG = struct.Struct("!QBB")


def hexload(path):
    with open(path, "rb") as f:
        return f.read().hex()


def name_message(flock, kek, name):
    """What Covenant signs, see vigil_config_name_verify() in vigil."""

    if isinstance(name, str):
        name = name.encode("utf-8")

    return NAME_LABEL + NAME_MSG.pack(flock, kek, len(name)) + name


def load_or_make_name_key(path):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Ed25519PrivateKey.from_private_bytes(f.read())

    key = Ed25519PrivateKey.generate()

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes_raw())

    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", required=True, help="where the keys live")
    ap.add_argument("--flock", required=True, type=lambda x: int(x, 16))
    ap.add_argument("--csid", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", required=True, type=int)
    ap.add_argument("--mtu", type=int, default=1408)
    ap.add_argument("--nat", action="store_true",
        help="also point the devices at the cathedral nat port")
    ap.add_argument("--sign", action="store_true",
        help="add a Covenant style signed name to each configuration")
    ap.add_argument("--out", help="where to write, default <lab>/clients")
    ap.add_argument("spi", nargs="+", type=lambda x: int(x, 16))

    args = ap.parse_args()

    lab = os.path.abspath(args.lab)
    out = args.out or os.path.join(lab, "clients")
    os.makedirs(out, exist_ok=True)

    kekdir = os.path.join(lab, "%016x" % args.flock, "kek-data")

    name_key = None
    name_pub = None

    if args.sign:
        name_key = load_or_make_name_key(os.path.join(lab, "keys",
            "name-ed25519"))
        name_pub = name_key.public_key().public_bytes_raw()

    for spi in args.spi:
        kek = os.path.join(kekdir, "kek-0x%02x" % spi)

        if not os.path.exists(kek):
            sys.exit("no kek for spi %02x at %s" % (spi, kek))

        instance = "device-%02x" % spi

        lines = [
            "vigil 1",
            "instance %s" % instance,
            "mode gospel",
            "spi %02x" % spi,
            "mtu %d" % args.mtu,
            "kek " + hexload(kek),
            "cathedral %s:%d" % (args.host, args.port),
            "cathedral_id %s" % args.csid,
            "cathedral_flock %016x" % args.flock,
            "cathedral_secret " + hexload(os.path.join(lab, "keys",
                "cs-%s" % args.csid)),
            "cathedral_cosk " + hexload(os.path.join(lab, "keys",
                "cosk-%s" % args.csid)),
        ]

        if args.nat:
            lines.append("cathedral_nat_port %d" % (args.port + 1))

        if name_key is not None:
            sig = name_key.sign(name_message(args.flock, spi, instance))
            lines.append("name_pubkey " + name_pub.hex())
            lines.append("name_signature " + sig.hex())

        path = os.path.join(out, "gospel-%02x.conf" % spi)

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

        print("%s  (%s%s)" % (path, instance,
            ", signed" if name_key is not None else ""))


if __name__ == "__main__":
    main()
