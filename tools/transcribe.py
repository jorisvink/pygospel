#!/usr/bin/env python3

"""
Print the words of an audio file, on this machine and nowhere else.

    $ python3 -m venv ~/whisper
    $ ~/whisper/bin/pip install faster-whisper 'av<16'
    $ ~/whisper/bin/python tools/transcribe.py voice.m4a

The model is downloaded the first time it runs and cached. Give this to
examples/agent.py as --transcribe.
"""

import sys

from faster_whisper import WhisperModel


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: transcribe.py <audio file>")

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(sys.argv[1], vad_filter=True)

    print(" ".join(segment.text.strip() for segment in segments))

    return 0


if __name__ == "__main__":
    sys.exit(main())
