"""Synthetic local adapter exercising per-point reporting, never a real device."""
import json
import sys
import time
from pathlib import Path


def main():
    started = time.perf_counter()
    request_path, result_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text())
    if request.get("demo") is not True:
        raise ValueError("Synthetic adapter cannot run against a real contract")
    kind = request["task"]["kind"]
    folder = result_path.parent
    log = folder / "actions.log"
    log.write_text("Synthetic local checks only; no device or network.\n")
    if kind == "build":
        package = folder / "synthetic.hap"
        package.write_bytes(b"SYNTHETIC PACKAGE: NOT INSTALLABLE\n")
        result = {"package_path": str(package), "evidence": [str(package), str(log)]}
    else:
        settings = json.loads((Path(request["roots"]["tools"]) / "settings.json").read_text())
        source = Path(request["roots"]["harmony"]) / "dynamics.py"
        namespace = {}
        exec(compile(source.read_text(), str(source), "exec"), namespace)
        result = {"points": []}
        captured = {}
        if kind == "visual":
            for record in request["dependency_receipts"].values():
                receipt = json.loads(Path(record["path"]).read_text())
                if receipt["kind"] == "device":
                    captured.update({p["acceptance_id"]: p for p in receipt["point_results"]})
        for item in request["acceptance"]:
            if item["id"] not in request.get("requested_acceptance_ids", [a["id"] for a in request["acceptance"]]):
                continue
            if settings.get("omit_popup") and item["id"] == "photo-source-visual":
                continue
            evidence = [str(log)]
            prior = captured.get(item["id"].replace("-visual", "-device"))
            if prior:
                evidence += [r["path"] for r in prior["evidence"]]
            elif kind in {"device", "visual"}:
                shot = folder / (item["id"] + ".png")
                shot.write_bytes((Path(request["roots"]["android"]) / "baseline.png").read_bytes())
                evidence.append(str(shot))
            result["points"].append({"acceptance_id": item["id"], "status": "checked", "evidence": evidence,
                "observations": [{"assertion_id": assertion["id"], "actual": namespace["gift_quantities"]()
                    if assertion["id"] == "gift-values" else True} for assertion in item["assertions"]]})
        if kind in {"device", "visual"}:
            result["context"] = {"device_id": "synthetic", "system_version": "not-a-device", "app_id": "example",
                "account_ref": "synthetic", "normal_entry": False, "synthetic": True,
                "environment_digest": "synthetic-fixture-v1",
                "package_sha256": request["package"]["sha256"]}
    phase = "build" if kind == "build" else "compare" if kind == "visual" else "assert"
    result["phase_seconds"] = {phase: time.perf_counter()-started}
    result_path.write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
