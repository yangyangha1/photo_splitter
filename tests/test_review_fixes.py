import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from photo_splitter.detection import split_box_on_empty_gaps
from photo_splitter.io_utils import count_source_pages, iter_source_images
from photo_splitter.performance import jpeg_save_kwargs, plan_workers
from photo_splitter.runtime_backend import detect_runtime_environment, get_compute_backend
from photo_splitter.web_app import options_from_payload


class ReviewFixTests(unittest.TestCase):
    def test_iter_source_images_yields_pages_without_list_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.tif"
            first = Image.new("RGB", (10, 10), "white")
            second = Image.new("RGB", (10, 10), "black")
            first.save(path, save_all=True, append_images=[second])

            self.assertEqual(count_source_pages(path), 2)
            pages = iter_source_images(path)
            self.assertFalse(hasattr(pages, "__len__"))
            stem, image = next(pages)
            try:
                self.assertEqual(stem, "multi_p001")
                self.assertEqual(image.size, (10, 10))
            finally:
                image.close()
                close = getattr(pages, "close", None)
                if close:
                    close()

    def test_options_from_payload_clamps_bad_values(self):
        options = options_from_payload(
            {
                "dark_threshold": "bad",
                "min_area_ratio": -1,
                "white_threshold": 999,
                "skew_gain_percent": 999,
                "background_mode": "bad",
                "detection_strategy": "bad",
            }
        )

        self.assertEqual(options["dark_threshold"], 70)
        self.assertEqual(options["min_area_ratio"], 0.0001)
        self.assertEqual(options["white_threshold"], 255)
        self.assertEqual(options["skew_gain_percent"], 100)
        self.assertEqual(options["background_mode"], "auto")
        self.assertEqual(options["detection_strategy"], "balanced")

    def test_split_box_on_empty_gaps_has_depth_limit(self):
        mask = np.zeros((120, 120), dtype=bool)
        result = split_box_on_empty_gaps(mask, (0, 0, 120, 120), min_side=1, max_depth=1)

        self.assertGreaterEqual(len(result), 1)

    def test_runtime_backend_never_selects_cuda_route(self):
        backend = get_compute_backend()
        self.assertNotIn(backend, {"cupy-cuda", "opencv-cuda", "pending-cuda"})

        runtime = detect_runtime_environment(probe_accelerators=False)
        self.assertNotIn(runtime["compute_backend"], {"cupy-cuda", "opencv-cuda", "pending-cuda"})
        self.assertFalse(runtime["cuda_available"])
        self.assertFalse(runtime["cupy_cuda_available"])
        self.assertFalse(runtime["opencv_cuda_available"])

    def test_worker_plan_and_jpeg_env_overrides(self):
        with patch.dict("os.environ", {"PHOTO_SPLITTER_DETECT_WORKERS": "3"}, clear=False):
            plan = plan_workers("detect", 10)
        self.assertEqual(plan.count, 3)
        self.assertEqual(plan_workers("detect", 10, backend="opencv-opencl").count, 2)

        self.assertFalse(jpeg_save_kwargs(fast=True)["optimize"])
        with patch.dict("os.environ", {"PHOTO_SPLITTER_JPEG_OPTIMIZE": "1"}, clear=False):
            self.assertFalse(jpeg_save_kwargs(fast=True)["optimize"])
            self.assertTrue(jpeg_save_kwargs(fast=False)["optimize"])


if __name__ == "__main__":
    unittest.main()
