"""Deterministic board validation: python -m host.preview --help."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import random
import statistics
import sys

from .client import Preview, load_manifest


def host_source_hashes():
    package = Path(__file__).resolve().parent
    return {f"host/preview/{path.name}":
            hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for path in sorted(package.glob("*.py"))}


def cases(seed):
    rng = random.Random(seed)
    yield "signed_extreme", [[-128]], [[-128]]
    for name, m, n, k in (("odd", 3, 5, 7), ("non_square", 9, 6, 33),
                          ("edge_255", 31, 29, 255), ("maximum_random", 32, 32, 256)):
        a = [[rng.randint(-128, 127) for _ in range(k)] for _ in range(m)]
        b = [[rng.randint(-128, 127) for _ in range(n)] for _ in range(k)]
        yield name, a, b
    yield "maximum_extreme", [[-128] * 256 for _ in range(32)], [[-128] * 32 for _ in range(256)]


def save_results(directory, report):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["jobs"]:
        with (directory / "results.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(report["jobs"][0]))
            writer.writeheader()
            writer.writerows(report["jobs"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate the P4/T32 BRAM preview and save complete comparisons/counters.")
    parser.add_argument("--port", required=True, help="USB-UART port, for example COM11")
    parser.add_argument("--manifest", required=True, type=Path, help="build/preview/build.json")
    parser.add_argument("--output", type=Path, default=Path("build/preview/hw_results"))
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--repeats", type=int, default=1, help="jobs per case; subsequent jobs reuse BRAM inputs")
    parser.add_argument("--baud", type=int, help="must match the manifest; normally omit")
    parser.add_argument("--timeout", type=float, default=2.0, help="UART request timeout in seconds")
    parser.add_argument("--job-timeout", type=float, default=5.0)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.timeout <= 0 or args.job_timeout <= 0 or args.retries < 0:
        parser.error("positive repeats/timeouts and nonnegative retries required")
    preview = None
    report = {"schema_version": 1, "kind": "bram_preview_board_validation",
              "utc": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
              "port": args.port, "passed": False, "jobs": [],
              "measurement_scope": "BRAM-resident local engine; UART transfer times recorded separately",
              "host": {"python": platform.python_version(), "platform": platform.platform(),
                       "source_sha256_utf8_lf": host_source_hashes()}}
    try:
        manifest = load_manifest(args.manifest)
        baud = manifest["baud"]
        if args.baud is not None and args.baud != baud:
            raise ValueError("--baud differs from the bitstream build setting")
        report["build"] = manifest
        report["baud"] = baud
        report["host"]["pyserial"] = importlib.metadata.version("pyserial")
        preview = Preview.open(args.port, baud, args.timeout, args.retries)
        report["identity"] = preview.identify(manifest)
        report["identity_check"] = "UART BUILD_ID/geometry/clock and selected local bitstream SHA-256"
        next_job = 1
        for name, a, b in cases(args.seed):
            report["active_case"] = {"name": name, "m": len(a), "n": len(b[0]),
                                     "k": len(b), "first_job_id": next_job}
            save_results(args.output, report)

            def record(item):
                item["case"] = name
                report["jobs"].append(item)
                save_results(args.output, report)

            measurements = preview.benchmark(a, b, next_job, args.seed, args.repeats,
                                             args.job_timeout, on_result=record)
            next_job += args.repeats
            print(f"PASS {name}: {len(a)}x{len(b[0])} K={len(b)}, "
                  f"{measurements[-1]['job_cycles']} core cycles, {args.repeats} job(s)")
        report.pop("active_case", None)
        report["summary"] = []
        for name in dict.fromkeys(item["case"] for item in report["jobs"]):
            runs = [item for item in report["jobs"] if item["case"] == name]
            cycles = [item["job_cycles"] for item in runs]
            report["summary"].append({"case": name, "jobs": len(runs),
                                      "job_cycles_min": min(cycles), "job_cycles_max": max(cycles),
                                      "job_cycles_median": statistics.median(cycles)})
        report["passed"] = True
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr)
    finally:
        if preview is not None:
            report["transport"] = {"retries": preview.link.retry_count,
                                   "rejected_frames": preview.link.decoder.rejected}
            try:
                preview.close()
            except OSError as exc:
                report["passed"] = False
                report["close_error"] = str(exc)
        save_results(args.output, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
