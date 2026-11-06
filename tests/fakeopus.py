"""
A stand-in for opuslib. A frame of audio is one byte repeated, and that byte
is the packet, so a test can see which frame came out of the other end.
"""

APPLICATION_VOIP = 2048
SIGNAL_VOICE     = 3001
AUTO             = -1000

# What a repaired frame is made of.
FEC = 0xfe
PLC = 0xff


class OpusError(Exception):
    pass


class _Ctl:
    set_bitrate = "bitrate"
    set_signal = "signal"
    set_inband_fec = "fec"
    set_packet_loss_perc = "loss"


class _EncoderState:
    def __init__(self):
        self.ctl = {}
        self.max_bytes = None


class _DecoderState:
    pass


def _encode(state, pcm, samples, max_bytes):
    if len(pcm) != samples * 2:
        raise OpusError("bad frame size")

    state.max_bytes = max_bytes

    return pcm[:1]


def _decode(state, opus, length, samples, fec, channels=2):
    if opus is None and length != 0:
        raise OpusError("conceal has no length")

    return bytes([PLC]) * (samples * channels * 2)


class _EncoderApi:
    encode = staticmethod(_encode)

    @staticmethod
    def encoder_ctl(state, ctl, value):
        state.ctl[ctl] = value


class _DecoderApi:
    decode = staticmethod(_decode)


class api:
    ctl = _Ctl
    encoder = _EncoderApi
    decoder = _DecoderApi


class Encoder:
    def __init__(self, fs, channels, application):
        self.fs = fs
        self.channels = channels
        self.application = application
        self.encoder_state = _EncoderState()


class Decoder:
    def __init__(self, fs, channels):
        self.fs = fs
        self.channels = channels
        self.decoder_state = _DecoderState()

    def decode(self, opus, samples, decode_fec=False):
        value = FEC if decode_fec else opus[0]

        return bytes([value]) * (samples * self.channels * 2)
