#!/usr/bin/env python3

"""
Say things for a voice call, on this machine and nowhere else. It keeps its
voice loaded, so a sentence starts in a moment rather than a while.

    $ python3 -m venv ~/piper
    $ ~/piper/bin/pip install piper-tts numpy
    $ ~/piper/bin/python -m piper.download_voices en_US-lessac-medium \
        --data-dir ~/piper
    $ python3 examples/agent.py -c gospel.conf -a 02 --hear ... \
        --speak "$HOME/piper/bin/python tools/speak.py \
        $HOME/piper/en_US-lessac-medium.onnx"

A line of JSON on stdin, {"id": 1, "text": "..."}, is a sentence to say. Out
comes its audio as 16 bit signed mono PCM at 48 kHz, in chunks with a head of
two big endian 32 bit numbers: the id and the number of bytes that follow. A
chunk of no bytes says the sentence is done, and the one with id 0 that comes
first says the voice is loaded.
"""

import sys
import json
import struct
import argparse

import numpy
from piper import PiperVoice

OUT_RATE = 48000
HEAD     = struct.Struct("!II")


def emit(id, pcm=b""):
    sys.stdout.buffer.write(HEAD.pack(id, len(pcm)) + pcm)
    sys.stdout.buffer.flush()


def resample(pcm, rate):
    samples = numpy.frombuffer(pcm, dtype="<i2").astype("float32")
    count = len(samples) * OUT_RATE // rate
    out = numpy.interp(numpy.linspace(0, len(samples), count, endpoint=False),
        numpy.arange(len(samples)), samples)

    return out.astype("<i2").tobytes()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("voice", help="the .onnx of a piper voice")

    args = ap.parse_args()

    voice = PiperVoice.load(args.voice)

    emit(0)

    for line in sys.stdin:
        request = json.loads(line)

        for chunk in voice.synthesize(request["text"]):
            emit(request["id"], resample(chunk.audio_int16_bytes,
                chunk.sample_rate))

        emit(request["id"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
