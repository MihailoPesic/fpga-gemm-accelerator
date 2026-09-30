"""Portable core regression. Run with the Python environment in requirements-test.txt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RTL = [ROOT / "rtl/core" / name for name in
       ("gemm_pe.sv", "gemm_array.sv", "gemm_microtile.sv")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("pe", "p4", "p8"))
    parser.add_argument("--rtl-dir", type=Path, default=ROOT / "rtl/core")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build/test")
    args = parser.parse_args()
    from cocotb_tools.runner import get_runner
    import cocotb

    sources = [args.rtl_dir.resolve() / f.name for f in RTL]
    os.environ["PYTHONPATH"] = str(ROOT / "tb") + os.pathsep + os.environ.get("PYTHONPATH", "")
    sys.path.insert(0, str(ROOT / "tb"))
    cases = [("pe", "gemm_pe", "test_pe", {}),
             ("p4", "gemm_microtile", "test_microtile", {"P": 4}),
             ("p8", "gemm_microtile", "test_microtile", {"P": 8})]
    results = []
    for name, top, module, parameters in cases:
        if args.only and args.only != name:
            continue
        build = args.build_dir.resolve() / name
        runner = get_runner("icarus")
        runner.build(sources=sources, hdl_toplevel=top, parameters=parameters,
                     build_dir=build, build_args=["-g2012", "-Wall"], always=True,
                     timescale=("1ns", "1ps"))
        result_xml = runner.test(hdl_toplevel=top, test_module=module,
                                build_dir=build, test_dir=build,
                                extra_env={"GEMM_COVERAGE": str(build / "coverage.json")})
        tree = ET.parse(result_xml)
        tests = list(tree.iter("testcase"))
        if not tests or any(list(t.iter("failure")) or list(t.iter("error"))
                            or list(t.iter("skipped")) for t in tests):
            raise RuntimeError(f"{name} failed; inspect {result_xml}")
        results.append({"case": name, "tests": len(tests), "result": "PASS"})
    summary = {
        "python": platform.python_version(), "cocotb": cocotb.__version__,
        "simulator": subprocess.run(["iverilog", "-V"], capture_output=True,
                                    text=True, check=True).stdout.splitlines()[0],
        "source_sha256": {str(f.relative_to(args.rtl_dir.resolve())):
                          hashlib.sha256(f.read_bytes()).hexdigest() for f in sources},
        "results": results,
    }
    args.build_dir.mkdir(parents=True, exist_ok=True)
    (args.build_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
