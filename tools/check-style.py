#!/usr/bin/env python3

"""
Check the style the python in this tree holds itself to.

    $ python3 tools/check-style.py src/pygospel.py examples/*.py

Three rules, all of them about the module constants:

  - no tabs anywhere, python aligns with spaces
  - a run of constants has its = lined up, one space after the longest name
  - nothing goes past 79 columns

It also says which lines would change so you can fix them with
tools/align.py, which applies the same rules instead of checking them.
"""

import os
import re
import sys

CONST  = re.compile(r'^([A-Z][A-Z_0-9]+)[ \t]*=[ \t]*(.*)$')

MAXCOL = 79


def blocks(lines):
    """The indexes of each run of module level constants."""

    marks = [CONST.match(l) if not l[:1].isspace() else None for l in lines]
    idx = [i for i, m in enumerate(marks) if m]
    out, cur = [], []

    for i in idx:
        if cur and all(lines[k].strip() == "" for k in range(cur[-1] + 1, i)):
            cur.append(i)
        else:
            out.append((cur, marks))
            cur = [i]

    if cur:
        out.append((cur, marks))

    return out


def check(path):
    with open(path) as f:
        lines = f.read().split("\n")

    problems = []

    for n, line in enumerate(lines, 1):
        if "\t" in line:
            problems.append("%s:%d: tab, use spaces" % (path, n))

        if len(line) > MAXCOL:
            problems.append("%s:%d: %d columns, %d is the most" % (path, n,
                len(line), MAXCOL))

        if line.rstrip() != line:
            problems.append("%s:%d: trailing whitespace" % (path, n))

    for run, marks in blocks(lines):
        if not run:
            continue

        width = max(len(marks[k].group(1)) for k in run)

        for k in run:
            want = "%s = %s" % (marks[k].group(1).ljust(width),
                marks[k].group(2))
            if lines[k] != want:
                problems.append("%s:%d: not lined up, want '%s'"
                    % (path, k + 1, want))

    return problems


def main():
    paths = sys.argv[1:]

    if not paths:
        sys.exit("usage: check-style.py [file ...]")

    problems = []

    for path in paths:
        if not os.path.exists(path):
            sys.exit("no such file: %s" % path)

        problems += check(path)

    for problem in problems:
        print(problem)

    if problems:
        print("%d problems, run tools/align.py %s to fix the constants"
            % (len(problems), " ".join(paths)))
        return 1

    print("%d files, no problems" % len(paths))

    return 0


if __name__ == "__main__":
    sys.exit(main())
