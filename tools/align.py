#!/usr/bin/env python3

"""
Line up the module constants in the given files.

    $ python3 tools/align.py src/pygospel.py examples/*.py

The rule check-style.py enforces: a run of constants is one after another
with nothing but blank lines in between, and its = goes one space past the
longest name in that run. A comment starts a new run, so each labelled
group gets its own column.
"""

import os
import re
import sys

CONST = re.compile(r'^([A-Z][A-Z_0-9]+)[ \t]*=[ \t]*(.*)$')


def align(path):
    with open(path) as f:
        lines = f.read().split("\n")

    marks = [CONST.match(l) if not l[:1].isspace() else None for l in lines]
    idx = [i for i, m in enumerate(marks) if m]

    runs, cur = [], []
    for i in idx:
        if cur and all(lines[k].strip() == "" for k in range(cur[-1] + 1, i)):
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]

    if cur:
        runs.append(cur)

    changed = 0

    for run in runs:
        if not run:
            continue

        width = max(len(marks[k].group(1)) for k in run)

        for k in run:
            new = "%s = %s" % (marks[k].group(1).ljust(width),
                marks[k].group(2))
            if lines[k] != new:
                lines[k] = new
                changed += 1

        # Tabs in a constant line go away with the alignment.
        for k in run:
            lines[k] = lines[k].expandtabs()

    if changed:
        with open(path, "w") as f:
            f.write("\n".join(lines))

    return changed


def main():
    paths = sys.argv[1:]

    if not paths:
        sys.exit("usage: align.py [file ...]")

    total = 0

    for path in paths:
        if not os.path.exists(path):
            sys.exit("no such file: %s" % path)

        n = align(path)
        total += n

        if n:
            print("%s: %d lines lined up" % (path, n))

    if not total:
        print("nothing to do")

    return 0


if __name__ == "__main__":
    sys.exit(main())
