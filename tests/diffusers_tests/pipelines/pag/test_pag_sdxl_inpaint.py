"""Adapted from https://github.com/huggingface/diffusers/tree/main/tests//pipelines/pag/test_pag_sdxl_inpaint.py."""

import random
import sys
import unittest

import numpy as np
import torch
from ddt import data, ddt, unpack
from PIL import Image
from transformers import CLIPTextConfig, CLIPVisionConfig

import mindspore as ms

from mindone.diffusers import StableDiffusionXLPAGInpaintPipeline
from mindone.diffusers.utils.testing_utils import (
    fast,
    load_downloaded_image_from_hf_hub,
    load_numpy_from_local_file,
    slow,
)

from ..pipeline_test_utils import (
    THRESHOLD_FP16,
    THRESHOLD_FP32,
    THRESHOLD_PIXEL,
    PipelineTesterMixin,
    floats_tensor,
    get_module,
    get_pipeline_components,
    randn_tensor,
)

test_cases = [
    {"mode": ms.PYNATIVE_MODE, "dtype": "float32"},
    {"mode": ms.PYNATIVE_MODE, "dtype": "float16"},
    {"mode": ms.GRAPH_MODE, "dtype": "float32"},
    {"mode": ms.GRAPH_MODE, "dtype": "float16"},
]


@fast
@ddt
class StableDiffusionXLPAGInpaintPipelineFastTests(
    PipelineTesterMixin,
    unittest.TestCase,
):
    pipeline_config = [
        [
            "scheduler",
            "diffusers.schedulers.scheduling_euler_discrete.EulerDiscreteScheduler",
            "mindone.diffusers.schedulers.scheduling_euler_discrete.EulerDiscreteScheduler",
            dict(
                beta_start=0.00085,
                beta_end=0.012,
                steps_offset=1,
                beta_schedule="scaled_linear",
                timestep_spacing="leading",
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
                    # SD2-specific config below
                    hidden_act="gelu",
                    projection_dim=32,
                ),
            ),
        ],
        [
            "text_encoder_2",
            "transformers.models.clip.modeling_clip.CLIPTextModelWithProjection",
            "mindone.transformers.models.clip.modeling_clip.CLIPTextModelWithProjection",
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
                    # SD2-specific config below
                    hidden_act="gelu",
                    projection_dim=32,
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
            "tokenizer_2",
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
                    image_size=224,
                    projection_dim=32,
                    intermediate_size=37,
                    num_attention_heads=4,
                    num_channels=3,
                    num_hidden_layers=5,
                    patch_size=14,
                ),
            ),
        ],
        [
            "feature_extractor",
            "transformers.models.clip.image_processing_clip.CLIPImageProcessor",
            "transformers.models.clip.image_processing_clip.CLIPImageProcessor",
            dict(
                crop_size=224,
                do_center_crop=True,
                do_normalize=True,
                do_resize=True,
                image_mean=[0.48145466, 0.4578275, 0.40821073],
                image_std=[0.26862954, 0.26130258, 0.27577711],
                resample=3,
                size=224,
            ),
        ],
    ]

    def get_unet_config(self, skip_first_text_encoder=False, time_cond_proj_dim=None, requires_aesthetics_score=False):
        return [
            "unet",
            "diffusers.models.unets.unet_2d_condition.UNet2DConditionModel",
            "mindone.diffusers.models.unets.unet_2d_condition.UNet2DConditionModel",
            dict(
                block_out_channels=(32, 64),
                layers_per_block=2,
                sample_size=32,
                in_channels=4,
                out_channels=4,
                time_cond_proj_dim=time_cond_proj_dim,
                down_block_types=("DownBlock2D", "CrossAttnDownBlock2D"),
                up_block_types=("CrossAttnUpBlock2D", "UpBlock2D"),
                # SD2-specific config below
                attention_head_dim=(2, 4),
                use_linear_projection=True,
                addition_embed_type="text_time",
                addition_time_embed_dim=8,
                transformer_layers_per_block=(1, 2),
                projection_class_embeddings_input_dim=72 if requires_aesthetics_score else 80,  # 5 * 8 + 32
                cross_attention_dim=64 if not skip_first_text_encoder else 32,
            ),
        ]

    def get_dummy_components(
        self, skip_first_text_encoder=False, time_cond_proj_dim=None, requires_aesthetics_score=False
    ):
        components = {
            key: None
            for key in [
                "unet",
                "scheduler",
                "vae",
                "text_encoder",
                "tokenizer",
                "text_encoder_2",
                "tokenizer_2",
                "image_encoder",
                "feature_extractor",
            ]
        }

        components["requires_aesthetics_score"] = (requires_aesthetics_score, requires_aesthetics_score)
        unet = self.get_unet_config(skip_first_text_encoder, time_cond_proj_dim, requires_aesthetics_score)
        pipeline_config = self.pipeline_config + [unet]

        pt_components, ms_components = get_pipeline_components(components, pipeline_config)

        if skip_first_text_encoder:
            pt_components["text_encoder"] = None
            pt_components["tokenizer"] = None
            ms_components["text_encoder"] = None
            ms_components["tokenizer"] = None

        return pt_components, ms_components

    def get_dummy_inputs(self, seed=0):
        # TODO: use tensor inputs instead of PIL, this is here just to leave the old expected_slices untouched
        image = floats_tensor((1, 3, 32, 32), rng=random.Random(seed))
        image = image.cpu().permute(0, 2, 3, 1)[0]
        init_image = Image.fromarray(np.uint8(image)).convert("RGB").resize((64, 64))
        # create mask
        image[8:, 8:, :] = 255
        mask_image = Image.fromarray(np.uint8(image)).convert("L").resize((64, 64))

        inputs = {
            "prompt": "A painting of a squirrel eating a burger",
            "image": init_image,
            "mask_image": mask_image,
            "num_inference_steps": 2,
            "guidance_scale": 6.0,
            "strength": 1.0,
            "pag_scale": 0.9,
            "output_type": "np",
        }
        return inputs

    @data(*test_cases)
    @unpack
    def test_inference(self, mode, dtype):
        ms.set_context(mode=mode)

        pt_components, ms_components = self.get_dummy_components(requires_aesthetics_score=True)
        pt_pipe_cls = get_module(
            "diffusers.pipelines.pag.pipeline_pag_sd_xl_inpaint.StableDiffusionXLPAGInpaintPipeline"
        )
        ms_pipe_cls = get_module(
            "mindone.diffusers.pipelines.pag.pipeline_pag_sd_xl_inpaint.StableDiffusionXLPAGInpaintPipeline"
        )

        pt_pipe = pt_pipe_cls(**pt_components, pag_applied_layers=["mid", "up", "down"])
        ms_pipe = ms_pipe_cls(**ms_components, pag_applied_layers=["mid", "up", "down"])

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
        assert np.max(np.linalg.norm(pt_image_slice - ms_image_slice) / np.linalg.norm(pt_image_slice)) < threshold


@slow
@ddt
class StableDiffusionXLPAGInpaintPipelineIntegrationTests(unittest.TestCase):
    @data(*test_cases)
    @unpack
    def test_pag_inference(self, mode, dtype):
        # TODO: synchronize issue, and we need to put the replacement of randn_tensor after initialization.
        if mode == ms.PYNATIVE_MODE:
            ms.set_context(mode=mode, pynative_synchronize=True)
        else:
            ms.set_context(mode=mode)
        ms_dtype = getattr(ms, dtype)

        pipe = StableDiffusionXLPAGInpaintPipeline.from_pretrained(
            "stabilityai/stable-diffusion-xl-base-1.0", mindspore_dtype=ms_dtype, enable_pag=True
        )

        sys.modules[pipe.__module__].randn_tensor = randn_tensor
        sys.modules[pipe.vae.diag_gauss_dist.__module__].randn_tensor = randn_tensor

        init_image = load_downloaded_image_from_hf_hub(
            "The-truth/mindone-testing-arrays",
            "inpaint_input.png",
            subfolder="stable_diffusion_xl",
        ).convert("RGB")
        mask_image = load_downloaded_image_from_hf_hub(
            "The-truth/mindone-testing-arrays",
            "inpaint_mask.png",
            subfolder="stable_diffusion_xl",
        ).convert("RGB")
        prompt = "A majestic tiger sitting on a bench"

        torch.manual_seed(0)
        image = pipe(
            prompt=prompt,
            image=init_image,
            mask_image=mask_image,
            num_inference_steps=50,
            strength=0.80,
            pag_scale=0.3,
        )[0][0]

        expected_image = load_numpy_from_local_file(
            "mindone-testing-arrays",
            f"sdxl_inpaint_{dtype}.npy",
            subfolder="pag",
        )
        assert np.mean(np.abs(np.array(image, dtype=np.float32) - expected_image)) < THRESHOLD_PIXEL
