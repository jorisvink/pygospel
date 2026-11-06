#!/usr/bin/env python3

"""
Listen to a voice call and print what was said, on this machine and nowhere
else. It keeps its model loaded, so it answers in a moment rather than a while.

    $ python3 -m venv ~/whisper
    $ ~/whisper/bin/pip install faster-whisper 'av<16' numpy
    $ python3 examples/agent.py -c gospel.conf -a 02 \
        --hear "$HOME/whisper/bin/python tools/hear.py" --speak ...

Audio comes in on stdin as 16 bit signed mono PCM at 48 kHz, for as long as the
call lasts. Out comes a line of JSON for each thing that happens:

    {"ready": true}         the model is loaded
    {"speech": true}        somebody started talking
    {"text": "..."}         they stopped and this is what they said, or "" if
                            it did not turn out to be words
"""

import os
import sys
import json
import argparse
import collections

import numpy
from faster_whisper import WhisperModel
from faster_whisper.vad import get_vad_model

IN_RATE  = 48000
RATE     = 16000
DECIMATE = IN_RATE // RATE
BLOCK    = 512
CONTEXT  = 64

# All of these count blocks of 32 ms.
PREROLL     = 10
SPEECH_MIN  = 8
SILENCE_MAX = 22
UTTERANCE   = 30 * RATE // BLOCK

LIKELY      = 0.5
NOT_SPEECH  = 0.6


def say(**fields):
    sys.stdout.write(json.dumps(fields) + "\n")
    sys.stdout.flush()


class Vad:
    """The Silero model of faster-whisper, keeping its state between blocks."""

    def __init__(self):
        self.session = get_vad_model().session
        self.h = numpy.zeros((1, 1, 128), dtype="float32")
        self.c = numpy.zeros((1, 1, 128), dtype="float32")
        self.context = numpy.zeros((1, CONTEXT), dtype="float32")

    def __call__(self, block):
        batch = numpy.concatenate([self.context, block.reshape(1, -1)], 1)
        out, self.h, self.c = self.session.run(None,
            {"input": batch, "h": self.h, "c": self.c})
        self.context = batch[:, -CONTEXT:]

        return float(numpy.ravel(out)[-1])


def words(model, language, blocks):
    audio = numpy.concatenate(blocks)
    segments, _ = model.transcribe(audio, language=language, beam_size=1,
        condition_on_previous_text=False)

    return " ".join(segment.text.strip() for segment in segments
        if segment.no_speech_prob < NOT_SPEECH).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--model", default="small", help="the whisper model")
    ap.add_argument("--device", default="cpu", help="cpu or cuda")
    ap.add_argument("--compute", default="int8", help="the compute type")
    ap.add_argument("--language", help="the language, or work it out")

    args = ap.parse_args()

    model = WhisperModel(args.model, device=args.device,
        compute_type=args.compute)
    vad = Vad()

    say(ready=True)

    raw = b""
    samples = numpy.zeros(0, dtype="float32")
    before = collections.deque(maxlen=PREROLL)
    blocks = []
    run = 0
    quiet = 0
    talking = False

    while True:
        data = os.read(0, 65536)

        if not data:
            return 0

        raw += data
        whole = len(raw) // (2 * DECIMATE) * (2 * DECIMATE)
        pcm = numpy.frombuffer(raw[:whole], dtype="<i2")
        raw = raw[whole:]

        down = pcm.reshape(-1, DECIMATE).mean(axis=1) / 32768.0
        samples = numpy.concatenate([samples, down.astype("float32")])

        while len(samples) >= BLOCK:
            block = samples[:BLOCK]
            samples = samples[BLOCK:]
            speech = vad(block) >= LIKELY

            if not talking:
                before.append(block)
                run = run + 1 if speech else 0

                if run >= SPEECH_MIN:
                    talking = True
                    quiet = 0
                    blocks = list(before)
                    say(speech=True)

                continue

            blocks.append(block)
            quiet = 0 if speech else quiet + 1

            if quiet >= SILENCE_MAX or len(blocks) >= UTTERANCE:
                say(text=words(model, args.language, blocks))
                talking = False
                run = 0
                blocks = []
                before.clear()


if __name__ == "__main__":
    sys.exit(main())
