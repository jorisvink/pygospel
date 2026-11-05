#!/usr/bin/env python3

"""
Draw what stdin describes into a PNG, on this machine and nowhere else.

    $ python3 -m venv ~/render
    $ ~/render/bin/pip install torch diffusers transformers accelerate gguf
    $ echo 'a lighthouse' | ~/render/bin/python tools/render.py a.png

The models are downloaded the first time it runs and cached, about 15 GiB
for Z-Image and 7 GiB for SDXL, so run it by hand once before giving it to
examples/agent.py as --render. It wants a GPU with about 12 GiB.
"""

import sys

import torch
import diffusers
import transformers

from diffusers import DPMSolverMultistepScheduler
from diffusers import GGUFQuantizationConfig
from diffusers import StableDiffusionXLImg2ImgPipeline
from diffusers import StableDiffusionXLPipeline
from diffusers import ZImagePipeline
from diffusers import ZImageTransformer2DModel
from PIL import Image

PHOTO       = "photograph, 50mm lens, natural light, natural skin texture"
SIZE        = 1024

ZIMAGE      = "Tongyi-MAI/Z-Image-Turbo"
ZIMAGE_GGUF = ("https://huggingface.co/unsloth/Z-Image-Turbo-GGUF/blob/main/"
    "z-image-turbo-Q8_0.gguf")
ZIMAGE_STEPS = 9

#SDXL     = "stabilityai/stable-diffusion-xl-base-1.0"
SDXL     = "SG161222/RealVisXL_V4.0"
HIRES    = 1344
STRENGTH = 0.3
STEPS    = 35
GUIDANCE = 4.5
NEGATIVE = ("lowres, blurry, jpeg artifacts, watermark, text, deformed, "
    "cartoon, 3d render, painting, plastic skin, extra arms, extra limbs, "
    "extra hands, extra fingers, bad anatomy, mutated body, duplicate")


def draw_zimage(prompt):
    quant = GGUFQuantizationConfig(compute_dtype=torch.bfloat16)
    transformer = ZImageTransformer2DModel.from_single_file(ZIMAGE_GGUF,
        quantization_config=quant, torch_dtype=torch.bfloat16, config=ZIMAGE,
        subfolder="transformer")

    pipe = ZImagePipeline.from_pretrained(ZIMAGE, transformer=transformer,
        torch_dtype=torch.bfloat16)

    pipe.set_progress_bar_config(disable=False)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()

    return pipe(prompt=prompt, width=SIZE, height=SIZE,
        num_inference_steps=ZIMAGE_STEPS, guidance_scale=0.0).images[0]


def draw_sdxl(prompt):
    pipe = StableDiffusionXLPipeline.from_pretrained(SDXL,
        torch_dtype=torch.float16, use_safetensors=True, variant="fp16")

    pipe.scheduler = DPMSolverMultistepScheduler.from_config(
        pipe.scheduler.config, use_karras_sigmas=True)
    pipe.set_progress_bar_config(disable=False)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()

    image = pipe(prompt=prompt, negative_prompt=NEGATIVE, width=SIZE,
        height=SIZE, num_inference_steps=STEPS,
        guidance_scale=GUIDANCE).images[0]

    refine = StableDiffusionXLImg2ImgPipeline(**pipe.components)
    refine.set_progress_bar_config(disable=False)
    refine.enable_model_cpu_offload()

    return refine(prompt=prompt, negative_prompt=NEGATIVE,
        image=image.resize((HIRES, HIRES), Image.LANCZOS), strength=STRENGTH,
        num_inference_steps=STEPS, guidance_scale=GUIDANCE).images[0]


DRAW = draw_zimage


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: render.py <output.png> < prompt")

    prompt = sys.stdin.read().strip()

    if not prompt:
        sys.exit("nothing to draw, the prompt goes on stdin")

    if not torch.cuda.is_available():
        sys.exit("no GPU that torch can use")

    diffusers.utils.logging.disable_progress_bar()
    transformers.utils.logging.disable_progress_bar()

    image = DRAW("%s, %s" % (prompt, PHOTO))
    image.save(sys.argv[1], "PNG", optimize=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
