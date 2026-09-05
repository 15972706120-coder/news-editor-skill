#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate cover crop and scaling geometry without editing any image.

The checker proves only that the declared crop and scale can fill the locked
cover region without geometric stretching or excessive upscaling.  It cannot
prove that the source was not already enlarged, that the declared transform
was actually used by the renderer, or that the image is visually sharp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError as error:  # pragma: no cover - exercised only in an incomplete runtime
    raise SystemExit(
        "缺少 Pillow，无法验证 PNG。请先在当前 Python 环境安装：python -m pip install Pillow"
    ) from error


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
OK_STATUS = "GEOMETRY_OK_REVIEW_REQUIRED"
BLOCKED_STATUS = "BLOCKED_VISUAL"
LIMITATIONS = [
    "几何检查不能证明原始帧没有在进入本流程前被预先放大。",
    "几何检查不能证明声明的裁切和缩放参数已由实际渲染工程执行。",
    "几何检查不能证明画面对焦准确、没有运动模糊、压缩块或其他清晰度问题。",
]


class InputValidationError(ValueError):
    """A stable, user-reportable validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if details:
        result["details"] = details
    return result


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _clean_number(value: float) -> int | float:
    rounded = round(float(value), 6)
    return int(rounded) if rounded.is_integer() else rounded


def _clean_pair(values: Sequence[float]) -> list[int | float]:
    return [_clean_number(value) for value in values]


def check_geometry(
    source_size: Sequence[float],
    crop: Sequence[float],
    scaled_size: Sequence[float],
    target_size: Sequence[float],
    *,
    preferred_max_upscale_ratio: float,
    hard_max_upscale_ratio: float,
    scale_ratio_tolerance: float,
) -> dict[str, Any]:
    """Pure numeric validation of a declared cover transform.

    ``crop`` is the clean usable rectangle in original-source coordinates.
    ``scaled_size`` is the actual width and height of that cropped effective
    layer after the crop -> scale transform.  It must cover the locked target
    region.  No alternative "complete source layer" meaning is supported.
    """

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    named_sequences = {
        "source_size": (source_size, 2),
        "crop": (crop, 4),
        "scaled_size": (scaled_size, 2),
        "target_size": (target_size, 2),
    }
    for name, (values, expected_count) in named_sequences.items():
        if len(values) != expected_count:
            failures.append(_issue("INVALID_VALUE_COUNT", f"{name} 必须包含 {expected_count} 个数值。"))
            continue
        for index, value in enumerate(values):
            if not _finite_number(value):
                failures.append(_issue("NON_FINITE_VALUE", f"{name}[{index}] 不是有限数值。"))

    threshold_values = {
        "preferred_max_upscale_ratio": preferred_max_upscale_ratio,
        "hard_max_upscale_ratio": hard_max_upscale_ratio,
        "scale_ratio_tolerance": scale_ratio_tolerance,
    }
    for name, value in threshold_values.items():
        if not _finite_number(value):
            failures.append(_issue("NON_FINITE_VALUE", f"{name} 不是有限数值。"))

    if failures:
        return {
            "status": BLOCKED_STATUS,
            "passed": False,
            "failures": failures,
            "warnings": warnings,
            "manual_review_required": True,
            "limitations": LIMITATIONS,
        }

    source_width, source_height = (float(value) for value in source_size)
    crop_x, crop_y, crop_width, crop_height = (float(value) for value in crop)
    scaled_width, scaled_height = (float(value) for value in scaled_size)
    target_width, target_height = (float(value) for value in target_size)
    preferred = float(preferred_max_upscale_ratio)
    hard = float(hard_max_upscale_ratio)
    tolerance = float(scale_ratio_tolerance)

    positive_values = {
        "source_width": source_width,
        "source_height": source_height,
        "crop_width": crop_width,
        "crop_height": crop_height,
        "scaled_width": scaled_width,
        "scaled_height": scaled_height,
        "target_width": target_width,
        "target_height": target_height,
        "preferred_max_upscale_ratio": preferred,
        "hard_max_upscale_ratio": hard,
    }
    for name, value in positive_values.items():
        if value <= 0:
            failures.append(_issue("NON_POSITIVE_DIMENSION", f"{name} 必须大于 0。", value=value))
    if crop_x < 0 or crop_y < 0:
        failures.append(_issue("NEGATIVE_CROP_ORIGIN", "裁切起点不得为负数。", crop_origin=[crop_x, crop_y]))
    if tolerance < 0 or tolerance >= 1:
        failures.append(
            _issue(
                "INVALID_TOLERANCE",
                "scale_ratio_tolerance 必须满足 0 <= tolerance < 1。",
                value=tolerance,
            )
        )
    if preferred > hard:
        failures.append(
            _issue(
                "INVALID_THRESHOLD_ORDER",
                "preferred_max_upscale_ratio 不得大于 hard_max_upscale_ratio。",
                preferred=preferred,
                hard=hard,
            )
        )

    safe_to_divide = crop_width > 0 and crop_height > 0
    scale_x = scaled_width / crop_width if safe_to_divide else None
    scale_y = scaled_height / crop_height if safe_to_divide else None

    if crop_width > 0 and crop_height > 0 and source_width > 0 and source_height > 0:
        if crop_x + crop_width > source_width + 1e-6 or crop_y + crop_height > source_height + 1e-6:
            failures.append(
                _issue(
                    "CROP_OUT_OF_BOUNDS",
                    "裁切区域超出原始帧边界。",
                    source_size=_clean_pair((source_width, source_height)),
                    crop=_clean_pair((crop_x, crop_y, crop_width, crop_height)),
                )
            )

    visible_source_size: list[int | float] | None = None
    visible_source_area: int | float | None = None
    stretch_error: int | float | None = None
    upscale_ratio: int | float | None = None

    if scale_x is not None and scale_y is not None and scale_x > 0 and scale_y > 0:
        relative_scale_error = abs(scale_x - scale_y) / max(scale_x, scale_y)
        stretch_error = _clean_number(relative_scale_error)
        if relative_scale_error > tolerance:
            failures.append(
                _issue(
                    "NON_UNIFORM_SCALE",
                    "横向与纵向缩放比例不一致，存在拉伸。",
                    scale_x=_clean_number(scale_x),
                    scale_y=_clean_number(scale_y),
                    relative_error=stretch_error,
                    tolerance=_clean_number(tolerance),
                )
            )

        upscale = max(scale_x, scale_y)
        upscale_ratio = _clean_number(upscale)
        if upscale > hard + 1e-9:
            failures.append(
                _issue(
                    "UPSCALE_RATIO_EXCEEDED",
                    "封面背景层放大倍率超过硬上限。",
                    actual=upscale_ratio,
                    hard_max=_clean_number(hard),
                )
            )
        elif upscale > preferred + 1e-9:
            warnings.append(
                _issue(
                    "UPSCALE_ABOVE_PREFERRED",
                    "封面背景层发生放大；虽然未超过硬上限，仍需优先比较更高清候选。",
                    actual=upscale_ratio,
                    preferred_max=_clean_number(preferred),
                )
            )

        if crop_width > 0 and crop_height > 0:
            if scaled_width + 1e-6 < target_width or scaled_height + 1e-6 < target_height:
                failures.append(
                    _issue(
                        "TARGET_UNDERFILLED",
                        "裁切后有效层的实际缩放尺寸不能完整覆盖锁定封面画面区。",
                        actual_scaled_cropped_layer_size=_clean_pair((scaled_width, scaled_height)),
                        target_size=_clean_pair((target_width, target_height)),
                    )
                )

            visible_width = min(crop_width, target_width / scale_x)
            visible_height = min(crop_height, target_height / scale_y)
            visible_source_size = _clean_pair((visible_width, visible_height))
            visible_source_area = _clean_number(max(0.0, visible_width) * max(0.0, visible_height))

    passed = not failures
    return {
        "status": OK_STATUS if passed else BLOCKED_STATUS,
        "passed": passed,
        "source_size": _clean_pair((source_width, source_height)),
        "crop": _clean_pair((crop_x, crop_y, crop_width, crop_height)),
        "actual_scaled_cropped_layer_size": _clean_pair((scaled_width, scaled_height)),
        "target_size": _clean_pair((target_width, target_height)),
        "scale_x": _clean_number(scale_x) if scale_x is not None else None,
        "scale_y": _clean_number(scale_y) if scale_y is not None else None,
        "scale_relative_error": stretch_error,
        "upscale_ratio": upscale_ratio,
        "visible_effective_source_pixels": {
            "size": visible_source_size,
            "area": visible_source_area,
        },
        "thresholds": {
            "preferred_max_upscale_ratio": _clean_number(preferred),
            "hard_max_upscale_ratio": _clean_number(hard),
            "scale_ratio_tolerance": _clean_number(tolerance),
        },
        "failures": failures,
        "warnings": warnings,
        "manual_review_required": True,
        "limitations": LIMITATIONS,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_png(path: Path, label: str) -> dict[str, Any]:
    """Read and decode-validate a PNG without modifying it."""

    if path.suffix.lower() != ".png":
        raise InputValidationError("NON_PNG_FILE", f"{label} 必须使用 .png 文件。")
    if not path.is_file():
        raise InputValidationError("IMAGE_NOT_FOUND", f"{label} 不存在或不是文件：{path}")

    try:
        with Image.open(path) as image:
            image_format = image.format
            image_size = image.size
            image_mode = image.mode
            image.verify()
        # verify() checks the container; reopening and load() forces pixel decode.
        with Image.open(path) as decoded:
            decoded.load()
            if decoded.format != image_format or decoded.size != image_size or decoded.mode != image_mode:
                raise ValueError("PNG 验证前后的格式、尺寸或色彩模式不一致")
            orientation = decoded.getexif().get(274, 1)
            has_transparency_metadata = "transparency" in decoded.info
            alpha_fully_opaque = None
            if decoded.mode == "RGBA":
                alpha_fully_opaque = decoded.getchannel("A").getextrema() == (255, 255)
    except (
        UnidentifiedImageError,
        OSError,
        RuntimeError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as error:
        raise InputValidationError("PNG_DECODE_ERROR", f"{label} 无法作为 PNG 完整解码：{error}") from error

    if image_format != "PNG":
        raise InputValidationError("NON_PNG_CONTENT", f"{label} 扩展名为 PNG，但实际格式是 {image_format or 'unknown'}。")
    if image_size[0] <= 0 or image_size[1] <= 0:
        raise InputValidationError("NON_POSITIVE_DIMENSION", f"{label} 的图像尺寸无效：{image_size}")
    if orientation not in (None, 1):
        raise InputValidationError(
            "EXIF_ORIENTATION_NOT_NORMALIZED",
            f"{label} 带有 EXIF Orientation={orientation}；请先归一为原生显示方向再检查。",
        )

    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "format": image_format,
        "mode": image_mode,
        "exif_orientation": orientation,
        "has_transparency_metadata": has_transparency_metadata,
        "alpha_fully_opaque": alpha_fully_opaque,
        "width": image_size[0],
        "height": image_size[1],
    }


def check_final_cover(cover_info: dict[str, Any], expected_size: Sequence[int]) -> list[dict[str, Any]]:
    """Validate final-cover dimensions and a platform-safe pixel format."""

    failures: list[dict[str, Any]] = []
    actual_size = (cover_info["width"], cover_info["height"])
    if actual_size != tuple(expected_size):
        failures.append(
            _issue(
                "FINAL_COVER_DIMENSION_MISMATCH",
                "完整封面尺寸与 config.video 画布不一致。",
                actual=list(actual_size),
                expected=list(expected_size),
            )
        )

    mode = cover_info.get("mode")
    alpha_fully_opaque = cover_info.get("alpha_fully_opaque")
    has_transparency_metadata = cover_info.get("has_transparency_metadata") is True
    if has_transparency_metadata or (mode == "RGBA" and alpha_fully_opaque is not True):
        failures.append(
            _issue(
                "FINAL_COVER_HAS_TRANSPARENCY",
                "最终封面不得包含透明像素或 tRNS 等透明度元数据。",
            )
        )
    elif mode not in {"RGB", "RGBA"}:
        failures.append(
            _issue(
                "FINAL_COVER_COLOR_MODE_UNSUPPORTED",
                "最终封面必须是 RGB，或全部不透明的 RGBA；灰度、索引色等模式不允许。",
                actual_mode=mode,
            )
        )
    return failures


def _parse_numbers(value: str, count: int, label: str) -> tuple[float, ...]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != count or any(not part for part in parts):
        raise InputValidationError("INVALID_ARGUMENT", f"{label} 必须包含 {count} 个逗号分隔数值。")
    try:
        return tuple(float(part) for part in parts)
    except ValueError as error:
        raise InputValidationError("INVALID_ARGUMENT", f"{label} 包含无法解析的数值。") from error


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))).casefold() == os.path.normcase(
        str(right.resolve(strict=False))
    ).casefold()


def _load_contract() -> tuple[dict[str, Any], dict[str, Any], Path]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        lock_path = ROOT / config["layout"]["active_lock_file"]
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        quality = config["cover"]["quality"]
        background = lock["cover"]["background_video"]
        target = {"width": background[2], "height": background[3]}
        canvas = {"width": config["video"]["width"], "height": config["video"]["height"]}
        thresholds = {
            "preferred_max_upscale_ratio": quality["preferred_max_upscale_ratio"],
            "hard_max_upscale_ratio": quality["hard_max_upscale_ratio"],
            "scale_ratio_tolerance": quality["scale_ratio_tolerance"],
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise InputValidationError("CONFIG_ERROR", f"无法读取封面几何配置：{error}") from error
    return {"target": target, "canvas": canvas, "thresholds": thresholds}, config, lock_path


def _base_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": BLOCKED_STATUS,
        "passed": False,
        "manual_review_required": True,
        "limitations": LIMITATIONS,
        "failures": [],
        "warnings": [],
    }


def _emit(report: dict[str, Any], report_path: Path | None) -> int:
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if report_path is not None:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(payload, encoding="utf-8")
        except OSError as error:
            report["status"] = BLOCKED_STATUS
            report["passed"] = False
            report.setdefault("failures", []).append(
                _issue("REPORT_WRITE_ERROR", f"无法写入报告：{error}", path=str(report_path))
            )
            payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    sys.stdout.write(payload)
    return 0 if report.get("passed") else 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_frame", type=Path, help="未处理的原始候选帧 PNG")
    parser.add_argument("--crop", required=True, help="原始帧中的有效裁切区域 x,y,w,h")
    parser.add_argument("--scaled-size", required=True, help="裁切后有效层的实际缩放尺寸 W,H（crop -> scale）")
    parser.add_argument("--cover", type=Path, help="可选的最终 9:16 完整封面 PNG")
    parser.add_argument("--report", type=Path, help="可选的工作区 JSON 报告路径")
    args = parser.parse_args(argv)

    report = _base_report()
    try:
        contract, config, lock_path = _load_contract()
    except InputValidationError as error:
        report["failures"].append(_issue(error.code, error.message))
        return _emit(report, None)

    protected_inputs = [args.source_frame, CONFIG_PATH, lock_path]
    if args.cover is not None:
        protected_inputs.append(args.cover)
    if args.report is not None and any(_same_path(args.report, path) for path in protected_inputs):
        report["failures"].append(
            _issue(
                "REPORT_OVERWRITES_INPUT",
                "报告路径不得覆盖 source_frame、--cover、config.json 或当前布局锁文件。",
            )
        )
        return _emit(report, None)

    if args.report is not None and args.report.suffix.lower() != ".json":
        report["failures"].append(
            _issue("REPORT_EXTENSION_INVALID", "--report 必须使用 .json 扩展名；未写入任何报告文件。")
        )
        return _emit(report, None)

    try:
        crop = _parse_numbers(args.crop, 4, "--crop")
        scaled_size = _parse_numbers(args.scaled_size, 2, "--scaled-size")
        source_info = inspect_png(args.source_frame, "source_frame")
        report["source_frame"] = source_info
        report["config"] = {
            "path": str(CONFIG_PATH.resolve()),
            "version": config.get("version"),
            "layout_lock": str(lock_path.resolve()),
            "target_size": [contract["target"]["width"], contract["target"]["height"]],
            "canvas_size": [contract["canvas"]["width"], contract["canvas"]["height"]],
        }

        geometry = check_geometry(
            (source_info["width"], source_info["height"]),
            crop,
            scaled_size,
            (contract["target"]["width"], contract["target"]["height"]),
            **contract["thresholds"],
        )
        report["geometry"] = geometry
        report["failures"].extend(geometry["failures"])
        report["warnings"].extend(geometry["warnings"])

        if args.cover is not None:
            cover_info = inspect_png(args.cover, "cover")
            report["cover"] = cover_info
            report["failures"].extend(
                check_final_cover(
                    cover_info,
                    (contract["canvas"]["width"], contract["canvas"]["height"]),
                )
            )
        else:
            report["warnings"].append(
                _issue(
                    "FULL_COVER_NOT_PROVIDED",
                    "未提供 --cover；本次不能验证最终 9:16 完整封面的文件格式和尺寸。",
                )
            )
    except InputValidationError as error:
        report["failures"].append(_issue(error.code, error.message))

    report["passed"] = not report["failures"]
    report["status"] = OK_STATUS if report["passed"] else BLOCKED_STATUS
    return _emit(report, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
