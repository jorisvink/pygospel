#!/bin/sh
#
# What agent.py needs for --transcribe, --hear, --speak and --render, each in
# a virtualenv of its own under $REQDIR, plus opuslib for taking calls in the
# one the agent runs from, and the models. Running it again only adds what is
# missing.
#

set -e

PYTHON=${PYTHON:-python3}
ROOTDIR=${REQDIR:-$HOME/.pygospel}
REQDIR=${REQDIR:-$ROOTDIR/agent}
LIBDIR=${LIBDIR:-$ROOTDIR/libs}
TOOLDIR=${TOOLDIR:-$ROOTDIR/tools}
AGENT_VENV=${AGENT_VENV:-$REQDIR/venv}
HF_HOME=${HF_HOME:-$REQDIR/huggingface}
VOICE=${VOICE:-en_US-lessac-medium}
WHISPER_MODEL=${WHISPER_MODEL:-small}

export HF_HOME

mkdir -p "$REQDIR"

WHISPER=$REQDIR/whisper
PIPER=$REQDIR/piper
RENDER=$REQDIR/render

if ! $PYTHON -c 'import ctypes.util, sys; \
    sys.exit(ctypes.util.find_library("opus") is None)'; then
	echo "no libopus found, install it (libopus0 or opus)"
	exit 1
fi

install() {
	dir=$1
	shift

	echo "== $dir"

	if [ ! -x "$dir/bin/python" ]; then
		$PYTHON -m venv "$dir" || {
			echo "cannot make a virtualenv, is python3-venv installed?"
			exit 1
		}
	fi

	"$dir/bin/python" -m pip install -q "$@"
}

install "$AGENT_VENV" opuslib==3.0.1
install "$WHISPER" faster-whisper==1.2.1 'av<16' numpy
install "$PIPER" piper-tts==1.8.0 numpy
install "$RENDER" torch diffusers transformers accelerate gguf

"$RENDER/bin/python" -c 'from diffusers import ZImagePipeline'

echo "== whisper model $WHISPER_MODEL"
"$WHISPER/bin/python" -c 'import sys
from faster_whisper import WhisperModel
WhisperModel(sys.argv[1], device="cpu", compute_type="int8")' "$WHISPER_MODEL"

if [ ! -f "$PIPER/$VOICE.onnx" ]; then
	echo "== voice $VOICE"
	"$PIPER/bin/python" -m piper.download_voices "$VOICE" \
	    --data-dir "$PIPER"
fi

mkdir -p $TOOLDIR

cp tools/hear.py $TOOLDIR
cp tools/speak.py $TOOLDIR
cp tools/render.py $TOOLDIR
cp tools/transcribe.py $TOOLDIR

cp examples/agent.py $REQDIR

mkdir -p $LIBDIR
cp build/host/libkyrka/obj/python/libkyrka.so $LIBDIR

cat << EOF

All installed. The render models are 15 GiB and come down the first time you
draw something, which takes longer than an agent waits, so do that once first:

  echo 'a lighthouse' | HF_HOME=$HF_HOME $RENDER/bin/python \\
    tools/render.py /tmp/a.png

Then:

  HF_HOME=$HF_HOME PYTHONPATH=build/host/libkyrka/obj/python \\
    $AGENT_VENV/bin/python examples/agent.py -c gospel.conf -a 02 --liturgy \\
    --transcribe "$WHISPER/bin/python tools/transcribe.py" \\
    --hear "$WHISPER/bin/python tools/hear.py" \\
    --speak "$PIPER/bin/python tools/speak.py $PIPER/$VOICE.onnx" \\
    --render "$RENDER/bin/python tools/render.py"
EOF
