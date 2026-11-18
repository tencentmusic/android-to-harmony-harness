"""Deterministic screenshot comparison; image scores do not grant run acceptance."""
import math
import warnings
from pathlib import Path

import runtime
from foundation import file_record, identifier, read_json, require, text

FORMAT = "migration-visual-comparison/1"
METRIC = "masked-windowed-ssim+mae"
THRESHOLDS = {"minimum_ssim": .85, "maximum_normalized_mae": .12}


def _libraries():
    try:
        import numpy as np
        import PIL
        import skimage
        from PIL import Image, ImageDraw
        from skimage.metrics import structural_similarity
    except ImportError as exc:
        raise ValueError("Visual comparison requires Pillow, numpy and scikit-image") from exc
    return np, Image, ImageDraw, structural_similarity, {
        "Pillow": PIL.__version__, "numpy": np.__version__, "scikit-image": skimage.__version__}


def _image(path, np, Image):
    record = file_record(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(record["path"]) as image:
                require(image.format in {"PNG", "JPEG"}, "Visual inputs must be PNG or JPEG")
                require(getattr(image, "n_frames", 1) == 1, "Animated visual inputs are unsupported")
                image.load()
                if "A" in image.getbands() or "transparency" in image.info:
                    require(image.convert("RGBA").getchannel("A").getextrema() == (255, 255),
                            "Visual inputs must be opaque")
                pixels = np.array(image.convert("RGB"), dtype=np.uint8)
                record.update(width=image.width, height=image.height, format=image.format)
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Cannot decode visual input: " + str(path)) from exc
    require(file_record(path)["sha256"] == record["sha256"], "Visual input changed while decoding")
    return pixels, record


def _rectangle(value, width, height, *, label, reason=False):
    require(isinstance(value, dict) and identifier(value.get("id")), "Invalid " + label + " id")
    result = {"id": value["id"]}
    for key in ("x", "y", "width", "height"):
        coordinate = value.get(key)
        require(type(coordinate) is int, label + " coordinates must be integers")
        result[key] = coordinate
    require(result["x"] >= 0 and result["y"] >= 0 and result["width"] > 0 and result["height"] > 0,
            "Invalid " + label + " rectangle")
    require(result["x"] + result["width"] <= width and result["y"] + result["height"] <= height,
            label + " rectangle exceeds viewport")
    if reason:
        require(text(value.get("reason")), "Dynamic masks require a reason")
        result["reason"] = value["reason"]
    require(set(value) == set(result), "Unknown " + label + " fields")
    return result


def _regions(value, width, height):
    if value is None:
        value = {"viewport": {"width": width, "height": height}, "masks": [],
                 "required_rois": [{"id": "viewport", "x": 0, "y": 0, "width": width, "height": height}]}
    require(isinstance(value, dict) and set(value) == {"viewport", "masks", "required_rois"},
            "Declare viewport, masks and required_rois")
    viewport = value["viewport"]
    require(isinstance(viewport, dict) and set(viewport) == {"width", "height"}
            and all(type(viewport[key]) is int for key in ("width", "height"))
            and viewport == {"width": width, "height": height}, "Visual viewport does not match input dimensions")
    require(isinstance(value["masks"], list), "masks must be a list")
    require(isinstance(value["required_rois"], list) and value["required_rois"], "Declare non-empty required_rois")
    result = {"viewport": dict(viewport)}
    for key, label in (("masks", "mask"), ("required_rois", "ROI")):
        result[key] = [_rectangle(v, width, height, label=label, reason=key == "masks") for v in value[key]]
        require(len({v["id"] for v in result[key]}) == len(result[key]), "Duplicate " + label + " ids")
    return result


def validate_regions(value):
    """Validate a reviewed region policy before any images or devices are used."""
    require(isinstance(value, dict) and isinstance(value.get("viewport"), dict), "Declare a visual viewport")
    return _regions(value, value["viewport"].get("width"), value["viewport"].get("height"))


def _include(regions, np):
    viewport = regions["viewport"]
    include = np.ones((viewport["height"], viewport["width"]), dtype=bool)
    for mask in regions["masks"]:
        include[mask["y"]:mask["y"] + mask["height"], mask["x"]:mask["x"] + mask["width"]] = False
    require(int(include.sum()) >= max(3, math.ceil(include.size * .05)),
            "Visual masks leave less than 5% comparable area")
    return include


def _window_centers(include, window, np):
    if bool(include.all()):
        return np.ones((include.shape[0] - window + 1, include.shape[1] - window + 1), dtype=bool)
    integral = np.pad(include, ((1, 0), (1, 0))).cumsum(axis=0, dtype=np.int32).cumsum(axis=1, dtype=np.int32)
    sums = (integral[window:, window:] - integral[:-window, window:]
            - integral[window:, :-window] + integral[:-window, :-window])
    return sums == window * window


def _measure(left, right, include, np, structural_similarity):
    require(left.shape == right.shape and left.ndim == 3 and left.shape[2] == 3
            and left.shape[:2] == include.shape and include.dtype == np.bool_,
            "Visual inputs and mask must have identical dimensions")
    require(bool(np.isfinite(left).all()) and bool(np.isfinite(right).all()), "Non-finite visual pixels")
    require(bool(((left >= 0) & (left <= 255)).all()) and bool(((right >= 0) & (right <= 255)).all()),
            "Visual pixels must be in [0, 255]")
    require(bool(include.any()), "Visual region has no unmasked pixels")
    smallest = min(left.shape[:2])
    window = min(7, smallest if smallest % 2 else smallest - 1)
    require(window >= 3, "Visual region is too small for windowed SSIM")
    centers = _window_centers(include, window, np)
    require(bool(centers.any()), "Visual region has no fully unmasked SSIM window")
    left, right = left.astype(np.float32), right.astype(np.float32)
    # Exclude every window touching a dynamic mask, including its surrounding centers.
    _, score_map = structural_similarity(left, right, data_range=255., channel_axis=2,
                                         win_size=window, full=True)
    border = window // 2
    score_map = np.mean(score_map, axis=2)[border:-border, border:-border]
    score = float(np.mean(score_map[centers], dtype=np.float64))
    mae = float(np.mean(np.abs(left - right)[include], dtype=np.float64) / 255.)
    require(math.isfinite(score) and math.isfinite(mae), "Non-finite visual metrics")
    score, mae = max(0., min(1., score)), max(0., min(1., mae))
    return {"score": score, "normalized_mae": mae, "ssim_window_size": window,
            "ssim_windows": int(centers.sum()), "compared_pixels": int(include.sum()),
            "status": "passed" if score >= THRESHOLDS["minimum_ssim"] and mae <= THRESHOLDS["maximum_normalized_mae"] else "failed"}


def _compute(baseline_path, capture_path, state_id, regions, diagnostic):
    require(identifier(state_id), "Invalid visual state_id")
    require(type(diagnostic) is bool, "diagnostic must be boolean")
    np, Image, ImageDraw, similarity, versions = _libraries()
    left, baseline = _image(baseline_path, np, Image)
    right, capture = _image(capture_path, np, Image)
    require(left.shape == right.shape, "Visual input dimensions differ; implicit crop/resize is forbidden")
    regions = _regions(regions, baseline["width"], baseline["height"])
    include = _include(regions, np)
    full = _measure(left, right, include, np, similarity)
    rois = []
    for roi in regions["required_rois"]:
        ys, xs = slice(roi["y"], roi["y"] + roi["height"]), slice(roi["x"], roi["x"] + roi["width"])
        rois.append({"id": roi["id"], **_measure(left[ys, xs], right[ys, xs], include[ys, xs], np, similarity)})
    report = {"format": FORMAT, "state_id": state_id, "metric": METRIC,
              "algorithm_version": 1, "implementation_sha256": runtime.file_hash(Path(__file__)),
              "dependencies": versions, "thresholds": dict(THRESHOLDS),
              "diagnostic": diagnostic, "gate_eligible": not diagnostic,
              "acceptance_scope": "image_comparison_only", "inputs": {"baseline": baseline, "capture": capture},
              "regions": regions, "full": full, "required_rois": rois,
              "status": "passed" if all(r["status"] == "passed" for r in [full, *rois]) else "failed"}
    for record in report["inputs"].values():
        require(file_record(record["path"])["sha256"] == record["sha256"], "Visual input changed during comparison")
    return report, left, right, include, (np, Image, ImageDraw)


def _artifact_images(report, left, right, include, libraries):
    np, Image, ImageDraw = libraries
    baseline, capture = Image.fromarray(left), Image.fromarray(right)
    delta = np.max(np.abs(left.astype(np.int16) - right.astype(np.int16)), axis=2).astype(np.uint8)
    heat = np.zeros_like(left)
    heat[:, :, 0] = delta
    heat[~include] = (80, 80, 80)
    difference = Image.fromarray(heat)
    width, height = baseline.size
    column = max(width, 180)
    combined = Image.new("RGB", (column * 3, height + 28), "white")
    draw = ImageDraw.Draw(combined)
    for offset, (label, image) in enumerate((("Baseline", baseline), ("Capture", capture), ("Difference (masked: gray)", difference))):
        combined.paste(image, (offset * column, 28))
        draw.text((offset * column + 4, 8), label, fill="black")
    for roi, result in zip(report["regions"]["required_rois"], report["required_rois"]):
        for offset in range(3):
            x, y = roi["x"] + offset * column, roi["y"] + 28
            draw.rectangle((x, y, x + roi["width"] - 1, y + roi["height"] - 1),
                           outline="green" if result["status"] == "passed" else "red", width=1)
    return {"baseline": baseline, "capture": capture, "difference": difference, "comparison": combined}


def _render(output, report, left, right, include, libraries):
    artifacts = {}
    for name, image in _artifact_images(report, left, right, include, libraries).items():
        path = output / (name + ".png")
        with path.open("xb") as stream:
            image.save(stream, format="PNG")
        artifacts[name] = file_record(path)
    return artifacts


def compare_images(baseline_path, capture_path, output_dir, *, state_id, regions=None, diagnostic=False):
    """Compare real pixels and write immutable artifacts into a new directory."""
    output = Path(output_dir)
    require(not output.exists() and not output.is_symlink(), "Visual output directory must be new")
    report, left, right, include, libraries = _compute(baseline_path, capture_path, state_id, regions, diagnostic)
    output.mkdir(parents=True, exist_ok=False)
    report["artifacts"] = _render(output.resolve(), report, left, right, include, libraries)
    report["report_digest"] = runtime.digest(report)
    runtime.write_json(output / "comparison.json", report)
    return report


def verify_comparison_report(report_path):
    """Recompute image metrics and verify immutable inputs/artifacts; grant no gate."""
    report_path = Path(report_path)
    file_record(report_path)
    report = read_json(report_path)
    require(isinstance(report, dict) and report.get("format") == FORMAT, "Invalid visual comparison report")
    digest = {key: value for key, value in report.items() if key != "report_digest"}
    require(report.get("report_digest") == runtime.digest(digest), "Changed visual comparison report")
    try:
        computed, left, right, include, libraries = _compute(
            report["inputs"]["baseline"]["path"], report["inputs"]["capture"]["path"],
            report["state_id"], report["regions"], report["diagnostic"])
        artifacts = report["artifacts"]
        require(isinstance(artifacts, dict) and set(artifacts) == {"baseline", "capture", "difference", "comparison"},
                "Missing visual comparison artifacts")
        np, Image, _ = libraries
        images = _artifact_images(computed, left, right, include, libraries)
        for name, record in artifacts.items():
            expected = report_path.resolve().parent / (name + ".png")
            require(record == file_record(expected), "Changed visual comparison artifact: " + name)
            with Image.open(expected) as actual:
                require(actual.format == "PNG" and actual.mode == "RGB" and actual.size == images[name].size
                        and np.array_equal(np.asarray(actual), np.asarray(images[name])),
                        "Visual comparison artifact differs from recomputed pixels: " + name)
        computed["artifacts"] = artifacts
        computed["report_digest"] = runtime.digest(computed)
        require(computed == report, "Visual comparison differs from recomputed pixels or current algorithm")
    except (KeyError, TypeError) as exc:
        raise ValueError("Incomplete visual comparison report") from exc
    return report
