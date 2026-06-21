import unittest
from unittest.mock import patch

from PIL import Image

from photo_splitter.web_app import (
    apply_orientation_detection_after_boxes,
    apply_rotation_marker,
    batch_worker_count,
    box_payload,
    export_rotation_to_marker,
    marker_to_export_rotation,
)


class RotationMarkerTests(unittest.TestCase):
    def test_marker_to_export_rotation_mapping(self):
        self.assertEqual(marker_to_export_rotation(None), 0)
        self.assertEqual(marker_to_export_rotation(0), 0)
        self.assertEqual(marker_to_export_rotation(90), 270)
        self.assertEqual(marker_to_export_rotation(180), 180)
        self.assertEqual(marker_to_export_rotation(270), 90)

    def test_export_rotation_to_marker_mapping(self):
        self.assertEqual(export_rotation_to_marker(0), 0)
        self.assertEqual(export_rotation_to_marker(90), 270)
        self.assertEqual(export_rotation_to_marker(180), 180)
        self.assertEqual(export_rotation_to_marker(270), 90)

    def test_box_payload_preserves_rotation_fields(self):
        self.assertEqual(
            box_payload([1.2, 2.6, 30, 40, 90, 0.5, "yunet", True, True]),
            [1, 3, 30, 40, 90, 0.5, "yunet", True, True],
        )

    def test_manual_box_defaults_to_no_marker(self):
        self.assertEqual(box_payload([0, 0, 40, 30])[4], None)

    def test_rotation_marker_uses_matrix_rotation(self):
        image = Image.new("RGB", (40, 20), "white")
        rotated = apply_rotation_marker(image, 90)
        self.assertEqual(rotated.size, (20, 40))

    def test_orientation_detection_disabled_keeps_no_marker(self):
        image = Image.new("RGB", (40, 20), "white")
        boxes = apply_orientation_detection_after_boxes(image, [(0, 0, 40, 20)], False)
        self.assertEqual(boxes[0][4], None)
        self.assertFalse(boxes[0][7])

    def test_worker_count_can_be_overridden(self):
        with patch.dict("os.environ", {"PHOTO_SPLITTER_DETECT_WORKERS": "3"}):
            workers, reason = batch_worker_count("detect", 10)
        self.assertEqual(workers, 3)
        self.assertEqual(reason, "PHOTO_SPLITTER_DETECT_WORKERS=3")


if __name__ == "__main__":
    unittest.main()
