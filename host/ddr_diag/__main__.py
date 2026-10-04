"""Run the bounded DDR diagnostic and save raw results."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys

from .client import DDRDiagnostic, load_manifest


def software_hashes():
    root = Path(__file__).resolve().parents[2]
    files = sorted((root / "host/ddr_diag").glob("*.py")) + [root / "host/preview/protocol.py"]
    return {path.relative_to(root).as_posix(): hashlib.sha256(
        path.read_text(encoding="utf-8").encode("utf-8")).hexdigest() for path in files}


def save(directory, report):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    rows = report["runs"]
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (directory / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=0x12345678,
                        help="first u32 seed; successive runs add 0x9e3779b9 modulo 2^32")
    parser.add_argument("--output", type=Path, default=Path("build/ddr_diag/hw_results"))
    parser.add_argument("--timeout", type=float, default=2.0, help="UART request timeout")
    parser.add_argument("--run-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.repeats < 1 or not 0 <= args.seed < (1 << 32) or args.timeout <= 0 or args.run_timeout <= 0:
        parser.error("positive repeats/timeouts and u32 seed required")
    report = {"schema_version": 1, "kind": "bounded_ddr_diagnostic", "passed": False,
              "utc": datetime.now(timezone.utc).isoformat(), "port": args.port, "runs": [],
              "scope": "4096 initialized bytes across 16 locations, including DDR-window edges; not a full-memory sweep or bandwidth/GEMM benchmark",
              "counter_semantics": {"read_beats": "64-bit words delivered by the burst engine",
                                    "write_beats": "64-bit words submitted to the burst engine",
                                    "cycles": "diagnostic engine clock cycles; UART/host time excluded"},
              "host": {"python": platform.python_version(), "platform": platform.platform(),
                       "source_sha256_utf8_lf": software_hashes()}}
    device = None
    try:
        manifest = load_manifest(args.manifest)
        report["build"] = manifest
        report["host"]["pyserial"] = version("pyserial")
        device = DDRDiagnostic.open(args.port, manifest["baud"], args.timeout)
        report["identity"] = device.identify(manifest)
        report["identity_check"] = "UART BUILD_ID/clock and selected local bitstream SHA-256"
        device.wait_ready(args.run_timeout)
        for index in range(args.repeats):
            seed = (args.seed + index*0x9e3779b9) & 0xffffffff
            report["active_run"] = {"index": index, "seed": seed}
            save(args.output, report)
            result = {"index": index, **device.run(seed, args.run_timeout)}
            report["runs"].append(result)
            report.pop("active_run", None)
            save(args.output, report)
            print(f"{'PASS' if result['passed'] else 'FAIL'} seed=0x{seed:08x}: {result}")
            if not result["passed"]:
                break
        report["passed"] = len(report["runs"]) == args.repeats and all(item["passed"] for item in report["runs"])
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr)
        if device is not None and not device.link.poisoned:
            try:
                report["failure_snapshot"] = device.snapshot()
            except Exception as snapshot_error:
                report["snapshot_error"] = str(snapshot_error)
    finally:
        if device is not None:
            report["transport"] = {"retries": device.link.retry_count,
                                   "rejected_frames": device.link.decoder.rejected}
            try:
                device.close()
            except OSError as exc:
                report["passed"] = False
                report["close_error"] = str(exc)
        save(args.output, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
