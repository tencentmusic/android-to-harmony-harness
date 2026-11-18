"""Standalone image checks, independent of migration approval and delivery state."""
from pathlib import Path

import foundation as base
from foundation import runtime
from visual import compare_images, _image, _libraries


def compare_capture(baseline, capture, output_dir, *, state_id, regions=None,
                    diagnostic=False, resize_to_baseline=False):
    output = Path(output_dir)
    base.require(not output.exists() and not output.is_symlink(), "Visual output directory must be new")
    output = output.resolve()
    base.require(not resize_to_baseline or diagnostic,
                 "Resolution normalization requires --diagnostic; it cannot grant native acceptance")
    transform = None
    if resize_to_baseline:
        np, Image, _, _, _ = _libraries()
        _, reference = _image(baseline, np, Image)
        pixels, source = _image(capture, np, Image)
        output.mkdir(parents=True, exist_ok=False)
        normalized = output / "normalized-capture.png"
        Image.fromarray(pixels).resize((reference["width"], reference["height"]), Image.Resampling.LANCZOS).save(normalized)
        transform = {"operation": "explicit_resize_to_baseline", "resampling": "LANCZOS",
                     "source": source, "output": base.file_record(normalized),
                     "from_size": [source["width"], source["height"]],
                     "to_size": [reference["width"], reference["height"]], "gate_eligible": False}
        runtime.write_json(output / "transform.json", transform)
        capture = normalized
    destination = output / "comparison" if transform else output
    report = compare_images(baseline, capture, destination, state_id=state_id,
                            regions=regions, diagnostic=diagnostic)
    return {"status": report["status"], "acceptance_scope": "image_comparison_only",
            "quality_gate_granted": False, "diagnostic": diagnostic,
            "comparison_report": base.file_record(destination / "comparison.json"),
            "comparison": report, **({"transform": transform} if transform else {})}
