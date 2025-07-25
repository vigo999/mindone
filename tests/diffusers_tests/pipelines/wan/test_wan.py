# Copyright 2025 The HuggingFace Team.
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

import unittest

import numpy as np
import pytest
import torch
from ddt import data, ddt, unpack
from PIL import Image

import mindspore as ms

from mindone.diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline
from mindone.diffusers.utils.testing_utils import load_numpy_from_local_file, slow

from ..pipeline_test_utils import (
    THRESHOLD_FP16,
    THRESHOLD_FP32,
    THRESHOLD_PIXEL,
    PipelineTesterMixin,
    get_module,
    get_pipeline_components,
)

test_cases = [
    {"mode": ms.PYNATIVE_MODE, "dtype": "float32"},
    {"mode": ms.PYNATIVE_MODE, "dtype": "float16"},
    {"mode": ms.PYNATIVE_MODE, "dtype": "bfloat16"},
    {"mode": ms.GRAPH_MODE, "dtype": "float32"},
    {"mode": ms.GRAPH_MODE, "dtype": "float16"},
    {"mode": ms.GRAPH_MODE, "dtype": "bfloat16"},
]


@ddt
class WanPipelineFastTests(PipelineTesterMixin, unittest.TestCase):
    pipeline_config = [
        [
            "vae",
            "diffusers.models.autoencoders.autoencoder_kl_wan.AutoencoderKLWan",
            "mindone.diffusers.models.autoencoders.autoencoder_kl_wan.AutoencoderKLWan",
            dict(
                base_dim=3,
                z_dim=16,
                dim_mult=[1, 1, 1, 1],
                num_res_blocks=1,
                temperal_downsample=[False, True, True],
            ),
        ],
        [
            "scheduler",
            "diffusers.schedulers.scheduling_flow_match_euler_discrete.FlowMatchEulerDiscreteScheduler",
            "mindone.diffusers.schedulers.scheduling_flow_match_euler_discrete.FlowMatchEulerDiscreteScheduler",
            dict(shift=7.0),
        ],
        [
            "text_encoder",
            "transformers.models.t5.modeling_t5.T5EncoderModel",
            "mindone.transformers.models.t5.modeling_t5.T5EncoderModel",
            dict(
                pretrained_model_name_or_path="hf-internal-testing/tiny-random-t5",
                revision="refs/pr/1",
            ),
        ],
        [
            "tokenizer",
            "transformers.models.auto.tokenization_auto.AutoTokenizer",
            "transformers.models.auto.tokenization_auto.AutoTokenizer",
            dict(
                pretrained_model_name_or_path="hf-internal-testing/tiny-random-t5",
            ),
        ],
        [
            "transformer",
            "diffusers.models.transformers.transformer_wan.WanTransformer3DModel",
            "mindone.diffusers.models.transformers.transformer_wan.WanTransformer3DModel",
            dict(
                patch_size=(1, 2, 2),
                num_attention_heads=2,
                attention_head_dim=12,
                in_channels=16,
                out_channels=16,
                text_dim=32,
                freq_dim=256,
                ffn_dim=32,
                num_layers=2,
                cross_attn_norm=True,
                qk_norm="rms_norm_across_heads",
                rope_max_seq_len=32,
            ),
        ],
    ]

    def get_dummy_components(self):
        components = {
            key: None
            for key in [
                "transformer",
                "scheduler",
                "vae",
                "text_encoder",
                "tokenizer",
            ]
        }

        return get_pipeline_components(components, self.pipeline_config)

    def get_dummy_inputs(self):
        inputs = {
            "prompt": "dance monkey",
            "negative_prompt": "negative",  # TODO
            "num_inference_steps": 2,
            "guidance_scale": 6.0,
            "height": 16,
            "width": 16,
            "num_frames": 9,
            "max_sequence_length": 16,
            "output_type": "np",
        }
        return inputs

    @data(*test_cases)
    @unpack
    def test_inference(self, mode, dtype):
        ms.set_context(mode=mode)

        pt_components, ms_components = self.get_dummy_components()
        pt_pipe_cls = get_module("diffusers.pipelines.wan.WanPipeline")
        ms_pipe_cls = get_module("mindone.diffusers.pipelines.wan.WanPipeline")

        pt_pipe = pt_pipe_cls(**pt_components)
        ms_pipe = ms_pipe_cls(**ms_components)

        pt_pipe.set_progress_bar_config(disable=None)
        ms_pipe.set_progress_bar_config(disable=None)

        ms_dtype, pt_dtype = getattr(ms, dtype), getattr(torch, dtype)
        pt_pipe = pt_pipe.to(pt_dtype)
        ms_pipe = ms_pipe.to(ms_dtype)

        inputs = self.get_dummy_inputs()

        torch.manual_seed(0)
        pt_frame = pt_pipe(**inputs)
        torch.manual_seed(0)
        ms_frame = ms_pipe(**inputs)

        pt_frame_slice = pt_frame.frames[0][0, -3:, -3:, -1]
        ms_frame_slice = ms_frame[0][0][0, -3:, -3:, -1]

        threshold = THRESHOLD_FP32 if dtype == "float32" else THRESHOLD_FP16
        assert np.linalg.norm(pt_frame_slice - ms_frame_slice) / np.linalg.norm(pt_frame_slice) < threshold


@slow
@ddt
class WanPipelineIntegrationTests(PipelineTesterMixin, unittest.TestCase):
    @data(*test_cases)
    @unpack
    def test_Wanx(self, mode, dtype):
        if dtype == "float32" or dtype == "float16":
            pytest.skip("Skipping this case since this pipeline will OOM in float32 and float16")

        ms.set_context(mode=mode)
        ms_dtype = getattr(ms, dtype)

        model_id = "Wan-AI/Wan2.1-T2V-14B-Diffusers"
        vae = AutoencoderKLWan.from_pretrained(model_id, subfolder="vae", mindspore_dtype=ms.float32)  # TODO
        pipe = WanPipeline.from_pretrained(model_id, vae=vae, mindspore_dtype=ms_dtype)
        flow_shift = 3.0  # 5.0 for 720P, 3.0 for 480P
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=flow_shift)

        prompt = "A cat and a dog baking a cake together in a kitchen. The cat is carefully measuring flour, while the dog is stirring the batter with a wooden spoon. The kitchen is cozy, with sunlight streaming through the window."  # noqa: E501
        negative_prompt = "Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards"  # noqa: E501

        torch.manual_seed(0)
        image = pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=480,
            width=720,
            num_frames=21,
            guidance_scale=5.0,
        )[0][0][1]
        image = Image.fromarray((image * 255).astype("uint8"))

        expected_image = load_numpy_from_local_file(
            "mindone-testing-arrays",
            f"wan_t2v_{dtype}.npy",
            subfolder="wan",
        )
        assert np.mean(np.abs(np.array(image, dtype=np.float32) - expected_image)) < THRESHOLD_PIXEL
