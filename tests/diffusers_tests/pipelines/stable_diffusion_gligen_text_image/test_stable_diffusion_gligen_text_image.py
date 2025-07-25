# coding=utf-8
# Copyright 2025 HuggingFace Inc.
#
# This code is adapted from https://github.com/huggingface/diffusers
# with modifications to run diffusers on mindspore.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import random
import unittest

import numpy as np
import torch
from ddt import data, ddt, unpack
from PIL import Image
from transformers import CLIPTextConfig, CLIPVisionConfig

import mindspore as ms

from mindone.diffusers.utils.testing_utils import load_downloaded_image_from_hf_hub, load_numpy_from_local_file, slow

from ..pipeline_test_utils import (
    THRESHOLD_FP16,
    THRESHOLD_FP32,
    THRESHOLD_PIXEL,
    PipelineTesterMixin,
    floats_tensor,
    get_module,
    get_pipeline_components,
)

test_cases = [
    {"mode": ms.PYNATIVE_MODE, "dtype": "float32"},
    {"mode": ms.PYNATIVE_MODE, "dtype": "float16"},
    {"mode": ms.GRAPH_MODE, "dtype": "float32"},
    {"mode": ms.GRAPH_MODE, "dtype": "float16"},
]


@ddt
class GligenTextImagePipelineFastTests(PipelineTesterMixin, unittest.TestCase):
    pipeline_config = [
        [
            "unet",
            "diffusers.models.unets.unet_2d_condition.UNet2DConditionModel",
            "mindone.diffusers.models.unets.unet_2d_condition.UNet2DConditionModel",
            dict(
                block_out_channels=(32, 64),
                layers_per_block=2,
                sample_size=32,
                in_channels=4,
                out_channels=4,
                down_block_types=("DownBlock2D", "CrossAttnDownBlock2D"),
                up_block_types=("CrossAttnUpBlock2D", "UpBlock2D"),
                cross_attention_dim=32,
                attention_type="gated-text-image",
            ),
        ],
        [
            "scheduler",
            "diffusers.schedulers.scheduling_ddim.DDIMScheduler",
            "mindone.diffusers.schedulers.scheduling_ddim.DDIMScheduler",
            dict(
                beta_start=0.00085,
                beta_end=0.012,
                beta_schedule="scaled_linear",
                clip_sample=False,
                set_alpha_to_one=False,
            ),
        ],
        [
            "vae",
            "diffusers.models.autoencoders.autoencoder_kl.AutoencoderKL",
            "mindone.diffusers.models.autoencoders.autoencoder_kl.AutoencoderKL",
            dict(
                block_out_channels=[32, 64],
                in_channels=3,
                out_channels=3,
                down_block_types=["DownEncoderBlock2D", "DownEncoderBlock2D"],
                up_block_types=["UpDecoderBlock2D", "UpDecoderBlock2D"],
                latent_channels=4,
                sample_size=128,
            ),
        ],
        [
            "text_encoder",
            "transformers.models.clip.modeling_clip.CLIPTextModel",
            "mindone.transformers.models.clip.modeling_clip.CLIPTextModel",
            dict(
                config=CLIPTextConfig(
                    bos_token_id=0,
                    eos_token_id=2,
                    hidden_size=32,
                    intermediate_size=37,
                    layer_norm_eps=1e-05,
                    num_attention_heads=4,
                    num_hidden_layers=5,
                    pad_token_id=1,
                    vocab_size=1000,
                ),
            ),
        ],
        [
            "tokenizer",
            "transformers.models.clip.tokenization_clip.CLIPTokenizer",
            "transformers.models.clip.tokenization_clip.CLIPTokenizer",
            dict(
                pretrained_model_name_or_path="hf-internal-testing/tiny-random-clip",
            ),
        ],
        [
            "image_encoder",
            "transformers.models.clip.modeling_clip.CLIPVisionModelWithProjection",
            "mindone.transformers.models.clip.modeling_clip.CLIPVisionModelWithProjection",
            dict(
                config=CLIPVisionConfig(
                    hidden_size=32,
                    projection_dim=32,
                    intermediate_size=37,
                    layer_norm_eps=1e-05,
                    num_attention_heads=4,
                    num_hidden_layers=5,
                ),
            ),
        ],
        [
            "processor",
            "transformers.models.clip.processing_clip.CLIPProcessor",
            "transformers.models.clip.processing_clip.CLIPProcessor",
            dict(
                pretrained_model_name_or_path="openai/clip-vit-large-patch14",
            ),
        ],
        [
            "image_project",
            "diffusers.pipelines.stable_diffusion.CLIPImageProjection",
            "mindone.diffusers.pipelines.stable_diffusion.CLIPImageProjection",
            dict(
                hidden_size=32,
            ),
        ],
    ]

    def get_dummy_components(self):
        components = {
            key: None
            for key in [
                "unet",
                "scheduler",
                "vae",
                "text_encoder",
                "tokenizer",
                "safety_checker",
                "feature_extractor",
                "image_encoder",
                "image_project",
                "processor",
            ]
        }

        return get_pipeline_components(components, self.pipeline_config)

    def get_dummy_inputs(self, seed=0):
        image = floats_tensor((1, 3, 640, 640), rng=random.Random(seed))
        image = image.cpu().permute(0, 2, 3, 1)[0]
        image = Image.fromarray(np.uint8(image)).convert("RGB")

        inputs = {
            "prompt": "A modern livingroom",
            "num_inference_steps": 2,
            "guidance_scale": 6.0,
            "gligen_phrases": ["a birthday cake"],
            "gligen_images": [image],
            "gligen_boxes": [[0.2676, 0.6088, 0.4773, 0.7183]],
            "output_type": "np",
        }
        return inputs

    @data(*test_cases)
    @unpack
    def test_stable_diffusion_gligen_text_image_default_case(self, mode, dtype):
        ms.set_context(mode=mode)

        pt_components, ms_components = self.get_dummy_components()
        pt_pipe_cls = get_module("diffusers.pipelines.stable_diffusion_gligen.StableDiffusionGLIGENTextImagePipeline")
        ms_pipe_cls = get_module(
            "mindone.diffusers.pipelines.stable_diffusion_gligen.StableDiffusionGLIGENTextImagePipeline"
        )

        pt_pipe = pt_pipe_cls(**pt_components)
        ms_pipe = ms_pipe_cls(**ms_components)

        pt_pipe.set_progress_bar_config(disable=None)
        ms_pipe.set_progress_bar_config(disable=None)

        ms_dtype, pt_dtype = getattr(ms, dtype), getattr(torch, dtype)
        pt_pipe = pt_pipe.to(pt_dtype)
        ms_pipe = ms_pipe.to(ms_dtype)

        inputs = self.get_dummy_inputs()

        torch.manual_seed(0)
        pt_image = pt_pipe(**inputs)
        torch.manual_seed(0)
        ms_image = ms_pipe(**inputs)

        pt_image_slice = pt_image.images[0, -3:, -3:, -1]
        ms_image_slice = ms_image[0][0, -3:, -3:, -1]

        threshold = THRESHOLD_FP32 if dtype == "float32" else THRESHOLD_FP16
        assert np.linalg.norm(pt_image_slice - ms_image_slice) / np.linalg.norm(pt_image_slice) < threshold


@slow
@ddt
class GligenTextImagePipelineIntegrationTests(PipelineTesterMixin, unittest.TestCase):
    @data(*test_cases)
    @unpack
    def test_gligen_text_image_inpainting_text_image_box(self, mode, dtype):
        ms.set_context(mode=mode)
        ms_dtype = getattr(ms, dtype)

        pipe_cls = get_module(
            "mindone.diffusers.pipelines.stable_diffusion_gligen.StableDiffusionGLIGENTextImagePipeline"
        )
        pipe = pipe_cls.from_pretrained(
            "anhnct/Gligen_Inpainting_Text_Image", revision="refs/pr/1", mindspore_dtype=ms_dtype
        )

        input_image = load_downloaded_image_from_hf_hub(
            "huggingface/documentation-images",
            "livingroom_modern.png",
            subfolder="diffusers/gligen",
        )
        prompt = "a backpack"
        boxes = [[0.2676, 0.4088, 0.4773, 0.7183]]
        phrases = None
        gligen_image = load_downloaded_image_from_hf_hub(
            "huggingface/documentation-images",
            "backpack.jpeg",
            subfolder="diffusers/gligen",
        )

        torch.manual_seed(0)
        image = pipe(
            prompt=prompt,
            gligen_phrases=phrases,
            gligen_inpaint_image=input_image,
            gligen_boxes=boxes,
            gligen_images=[gligen_image],
            gligen_scheduled_sampling_beta=1,
            num_inference_steps=50,
        )[0][0]

        expected_image = load_numpy_from_local_file(
            "mindone-testing-arrays",
            f"inpainting_{dtype}.npy",
            subfolder="stable_diffusion_gligen_text_image",
        )
        assert np.mean(np.abs(np.array(image, dtype=np.float32) - expected_image)) < THRESHOLD_PIXEL

    @data(*test_cases)
    @unpack
    def test_gligen_text_image_generation_text_image_box(self, mode, dtype):
        ms.set_context(mode=mode)
        ms_dtype = getattr(ms, dtype)

        pipe_cls = get_module(
            "mindone.diffusers.pipelines.stable_diffusion_gligen.StableDiffusionGLIGENTextImagePipeline"
        )
        pipe = pipe_cls.from_pretrained("anhnct/Gligen_Text_Image", revision="refs/pr/1", mindspore_dtype=ms_dtype)

        prompt = "a flower sitting on the beach"
        boxes = [[0.0, 0.09, 0.53, 0.76]]
        phrases = ["flower"]
        gligen_image = load_downloaded_image_from_hf_hub(
            "huggingface/documentation-images",
            "pexels-pixabay-60597.jpg",
            subfolder="diffusers/gligen",
        )

        torch.manual_seed(0)
        image = pipe(
            prompt=prompt,
            gligen_phrases=phrases,
            gligen_images=[gligen_image],
            gligen_boxes=boxes,
            gligen_scheduled_sampling_beta=1,
            num_inference_steps=50,
        )[0][0]

        expected_image = load_numpy_from_local_file(
            "mindone-testing-arrays",
            f"generation_{dtype}.npy",
            subfolder="stable_diffusion_gligen_text_image",
        )
        assert np.mean(np.abs(np.array(image, dtype=np.float32) - expected_image)) < THRESHOLD_PIXEL

    @data(*test_cases)
    @unpack
    def test_gligen_text_image_text_image_box_style_transfer(self, mode, dtype):
        ms.set_context(mode=mode)
        ms_dtype = getattr(ms, dtype)

        pipe_cls = get_module(
            "mindone.diffusers.pipelines.stable_diffusion_gligen.StableDiffusionGLIGENTextImagePipeline"
        )
        pipe = pipe_cls.from_pretrained("anhnct/Gligen_Text_Image", revision="refs/pr/1", mindspore_dtype=ms_dtype)

        prompt = "a dragon flying on the sky"
        boxes = [[0.4, 0.2, 1.0, 0.8], [0.0, 1.0, 0.0, 1.0]]  # Set `[0.0, 1.0, 0.0, 1.0]` for the style
        gligen_image = load_downloaded_image_from_hf_hub(
            "huggingface/documentation-images",
            "landscape.png",
            subfolder="diffusers",
        )
        gligen_placeholder = load_downloaded_image_from_hf_hub(
            "huggingface/documentation-images",
            "landscape.png",
            subfolder="diffusers",
        )

        torch.manual_seed(0)
        image = pipe(
            prompt=prompt,
            gligen_phrases=[
                "dragon",
                "placeholder",
            ],  # Can use any text instead of `placeholder` token, because we will use mask here
            gligen_images=[
                gligen_placeholder,
                gligen_image,
            ],  # Can use any image in gligen_placeholder, because we will use mask here
            input_phrases_mask=[1, 0],  # Set 0 for the placeholder token
            input_images_mask=[0, 1],  # Set 0 for the placeholder image
            gligen_boxes=boxes,
            gligen_scheduled_sampling_beta=1,
            num_inference_steps=50,
        )[0][0]

        expected_image = load_numpy_from_local_file(
            "mindone-testing-arrays",
            f"style_transfer_{dtype}.npy",
            subfolder="stable_diffusion_gligen_text_image",
        )
        assert np.mean(np.abs(np.array(image, dtype=np.float32) - expected_image)) < THRESHOLD_PIXEL
