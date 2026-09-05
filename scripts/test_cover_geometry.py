#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import check_cover_geometry as checker  # noqa: E402


VALID_SOURCE = ROOT / "assets/references/locked-layout/reference-cover-frame.png"
SMALL_COVER = ROOT / "assets/references/locked-layout/reference-cover-thumbnail-270x360.png"
JPEG_REFERENCE = ROOT / "assets/references/cover-grid-good-bad-reference.jpg"


def geometry(**overrides):
    values = {
        "source_size": (1080, 1920),
        "crop": (0, 240, 1080, 1440),
        "scaled_size": (1080, 1440),
        "target_size": (1080, 1440),
        "preferred_max_upscale_ratio": 1.0,
        "hard_max_upscale_ratio": 1.5,
        "scale_ratio_tolerance": 0.005,
    }
    values.update(overrides)
    return checker.check_geometry(**values)


def failure_codes(result):
    return {item["code"] for item in result["failures"]}


def warning_codes(result):
    return {item["code"] for item in result["warnings"]}


def png_with_exif_orientation(source: bytes, orientation: int) -> bytes:
    """Inject an eXIf chunk without using Pillow to create or edit test images."""

    ihdr_length = struct.unpack(">I", source[8:12])[0]
    insert_at = 8 + 12 + ihdr_length
    tiff = (
        struct.pack("<2sHI", b"II", 42, 8)
        + struct.pack("<H", 1)
        + struct.pack("<HHI", 274, 3, 1)
        + struct.pack("<H", orientation)
        + b"\x00\x00"
        + struct.pack("<I", 0)
    )
    chunk_type = b"eXIf"
    chunk = (
        struct.pack(">I", len(tiff))
        + chunk_type
        + tiff
        + struct.pack(">I", zlib.crc32(chunk_type + tiff) & 0xFFFFFFFF)
    )
    return source[:insert_at] + chunk + source[insert_at:]


class GeometryTests(unittest.TestCase):
    def test_normal_identity_scale_requires_review(self):
        result = geometry()
        self.assertTrue(result["passed"])
        self.assertEqual(result["status"], checker.OK_STATUS)
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["scale_x"], 1)
        self.assertEqual(result["scale_y"], 1)
        self.assertEqual(result["visible_effective_source_pixels"]["size"], [1080, 1440])

    def test_uniform_upscale_at_hard_limit_warns_but_passes(self):
        result = geometry(
            source_size=(720, 1280),
            crop=(0, 160, 720, 960),
            scaled_size=(1080, 1440),
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["upscale_ratio"], 1.5)
        self.assertIn("UPSCALE_ABOVE_PREFERRED", warning_codes(result))

    def test_crop_out_of_bounds_fails(self):
        result = geometry(crop=(1, 240, 1080, 1440))
        self.assertIn("CROP_OUT_OF_BOUNDS", failure_codes(result))

    def test_non_finite_value_fails_without_nan_json_values(self):
        result = geometry(crop=(0, float("nan"), 1080, 1440))
        self.assertIn("NON_FINITE_VALUE", failure_codes(result))
        self.assertNotIn("NaN", checker.json.dumps(result, allow_nan=False))

    def test_non_positive_dimension_fails(self):
        result = geometry(source_size=(0, 1920))
        self.assertIn("NON_POSITIVE_DIMENSION", failure_codes(result))

    def test_negative_crop_origin_fails(self):
        result = geometry(crop=(-1, 240, 1080, 1440))
        self.assertIn("NEGATIVE_CROP_ORIGIN", failure_codes(result))

    def test_underfill_fails(self):
        result = geometry(scaled_size=(900, 1200))
        self.assertIn("TARGET_UNDERFILLED", failure_codes(result))

    def test_non_uniform_scale_fails(self):
        result = geometry(scaled_size=(1080, 1400))
        self.assertIn("NON_UNIFORM_SCALE", failure_codes(result))

    def test_excessive_upscale_fails(self):
        result = geometry(
            source_size=(600, 1066.6666667),
            crop=(0, 133.3333333, 600, 800),
            scaled_size=(1080, 1440),
        )
        self.assertIn("UPSCALE_RATIO_EXCEEDED", failure_codes(result))

    def test_large_source_does_not_hide_small_crop_upscale(self):
        result = geometry(
            source_size=(4000, 3000),
            crop=(1200, 900, 600, 800),
            scaled_size=(1080, 1440),
        )
        self.assertIn("UPSCALE_RATIO_EXCEEDED", failure_codes(result))
        self.assertEqual(result["upscale_ratio"], 1.8)

    def test_invalid_threshold_order_fails(self):
        result = geometry(preferred_max_upscale_ratio=2.0, hard_max_upscale_ratio=1.5)
        self.assertIn("INVALID_THRESHOLD_ORDER", failure_codes(result))

    def test_tolerance_must_be_less_than_one(self):
        result = geometry(scale_ratio_tolerance=1.0)
        self.assertIn("INVALID_TOLERANCE", failure_codes(result))
        self.assertEqual(result["status"], "BLOCKED_VISUAL")


class PngValidationTests(unittest.TestCase):
    def test_reads_valid_png_without_editing(self):
        before = hashlib.sha256(VALID_SOURCE.read_bytes()).hexdigest()
        info = checker.inspect_png(VALID_SOURCE, "source")
        after = hashlib.sha256(VALID_SOURCE.read_bytes()).hexdigest()
        self.assertEqual(info["format"], "PNG")
        self.assertEqual((info["width"], info["height"]), (1080, 1920))
        self.assertEqual(before, after)

    def test_non_png_extension_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "frame.jpg"
            shutil.copyfile(VALID_SOURCE, path)
            with self.assertRaises(checker.InputValidationError) as raised:
                checker.inspect_png(path, "source")
            self.assertEqual(raised.exception.code, "NON_PNG_FILE")

    def test_png_extension_with_jpeg_content_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "frame.png"
            shutil.copyfile(JPEG_REFERENCE, path)
            with self.assertRaises(checker.InputValidationError) as raised:
                checker.inspect_png(path, "source")
            self.assertEqual(raised.exception.code, "NON_PNG_CONTENT")

    def test_corrupt_png_fails_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "frame.png"
            path.write_bytes(b"not a png")
            with self.assertRaises(checker.InputValidationError) as raised:
                checker.inspect_png(path, "source")
            self.assertEqual(raised.exception.code, "PNG_DECODE_ERROR")

    def test_non_default_exif_orientation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rotated.png"
            path.write_bytes(png_with_exif_orientation(VALID_SOURCE.read_bytes(), 6))
            with self.assertRaises(checker.InputValidationError) as raised:
                checker.inspect_png(path, "source")
            self.assertEqual(raised.exception.code, "EXIF_ORIENTATION_NOT_NORMALIZED")

    def test_final_cover_size_mismatch_fails(self):
        info = checker.inspect_png(SMALL_COVER, "cover")
        failures = checker.check_final_cover(info, (1080, 1920))
        self.assertEqual({item["code"] for item in failures}, {"FINAL_COVER_DIMENSION_MISMATCH"})

    def test_final_cover_rejects_transparency(self):
        info = {"width": 1080, "height": 1920, "mode": "RGBA", "alpha_fully_opaque": False}
        self.assertIn(
            "FINAL_COVER_HAS_TRANSPARENCY",
            {item["code"] for item in checker.check_final_cover(info, (1080, 1920))},
        )

    def test_final_cover_accepts_fully_opaque_rgba(self):
        info = {
            "width": 1080,
            "height": 1920,
            "mode": "RGBA",
            "alpha_fully_opaque": True,
            "has_transparency_metadata": False,
        }
        self.assertEqual(checker.check_final_cover(info, (1080, 1920)), [])

    def test_final_cover_rejects_rgb_transparency_key(self):
        info = {
            "width": 1080,
            "height": 1920,
            "mode": "RGB",
            "alpha_fully_opaque": None,
            "has_transparency_metadata": True,
        }
        self.assertIn(
            "FINAL_COVER_HAS_TRANSPARENCY",
            {item["code"] for item in checker.check_final_cover(info, (1080, 1920))},
        )

    def test_final_cover_rejects_grayscale(self):
        info = {"width": 1080, "height": 1920, "mode": "L", "alpha_fully_opaque": None}
        self.assertIn(
            "FINAL_COVER_COLOR_MODE_UNSUPPORTED",
            {item["code"] for item in checker.check_final_cover(info, (1080, 1920))},
        )

    def test_report_cannot_overwrite_source_input(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.png"
            shutil.copyfile(VALID_SOURCE, source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = checker.main(
                    [
                        str(source),
                        "--crop",
                        "0,240,1080,1440",
                        "--scaled-size",
                        "1080,1440",
                        "--report",
                        str(source),
                    ]
                )
            after = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(exit_code, 2)
            self.assertEqual(before, after)
            self.assertIn("REPORT_OVERWRITES_INPUT", stdout.getvalue())

    def test_report_cannot_overwrite_config_or_active_lock(self):
        _, _, lock_path = checker._load_contract()
        for protected in (checker.CONFIG_PATH, lock_path):
            with self.subTest(protected=protected):
                before = hashlib.sha256(protected.read_bytes()).hexdigest()
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = checker.main(
                        [
                            str(VALID_SOURCE),
                            "--crop",
                            "0,240,1080,1440",
                            "--scaled-size",
                            "1080,1440",
                            "--report",
                            str(protected),
                        ]
                    )
                after = hashlib.sha256(protected.read_bytes()).hexdigest()
                self.assertEqual(exit_code, 2)
                self.assertEqual(before, after)
                self.assertIn("REPORT_OVERWRITES_INPUT", stdout.getvalue())

    def test_report_requires_json_extension_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "report.png"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = checker.main(
                    [
                        str(VALID_SOURCE),
                        "--crop",
                        "0,240,1080,1440",
                        "--scaled-size",
                        "1080,1440",
                        "--report",
                        str(report_path),
                    ]
                )
            self.assertEqual(exit_code, 2)
            self.assertFalse(report_path.exists())
            self.assertIn("REPORT_EXTENSION_INVALID", stdout.getvalue())

    def test_cli_writes_geometry_report_without_editing_images(self):
        with tempfile.TemporaryDirectory() as temp:
            report_path = Path(temp) / "cover-geometry.json"
            source_before = hashlib.sha256(VALID_SOURCE.read_bytes()).hexdigest()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = checker.main(
                    [
                        str(VALID_SOURCE),
                        "--crop",
                        "0,240,1080,1440",
                        "--scaled-size",
                        "1080,1440",
                        "--cover",
                        str(VALID_SOURCE),
                        "--report",
                        str(report_path),
                    ]
                )
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            source_after = hashlib.sha256(VALID_SOURCE.read_bytes()).hexdigest()
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], checker.OK_STATUS)
            self.assertTrue(payload["manual_review_required"])
            self.assertEqual(payload["geometry"]["target_size"], [1080, 1440])
            self.assertEqual(source_before, source_after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
