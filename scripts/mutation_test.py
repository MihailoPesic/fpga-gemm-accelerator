"""Prove the core regressions reject three specific broken implementations."""
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = ROOT / "build/mutations" / str(time.time_ns())
    mutations = [
        ("unsigned_multiply", "pe", "gemm_pe.sv",
         "product <= a * b;", "product <= $unsigned(a) * $unsigned(b);"),
        ("product_survives_clear", "pe", "gemm_pe.sv",
         "product_valid <= !clear && a_valid && b_valid;",
         "product_valid <= a_valid && b_valid;"),
        ("early_drain", "p4", "gemm_microtile.sv",
         "9'(2*P-2)", "9'(2*P-3)"),
    ]
    results = []
    for name, case, file, before, after in mutations:
        directory = work / name
        rtl = directory / "rtl"
        rtl.mkdir(parents=True)
        for source in (ROOT / "rtl/core").glob("*.sv"):
            text = source.read_text()
            if source.name == file:
                if text.count(before) != 1:
                    raise RuntimeError(f"Mutation anchor changed: {name}")
                text = text.replace(before, after)
            (rtl / source.name).write_text(text)
        with (directory / "console.txt").open("w") as log:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/test.py"), "--only", case,
                 "--rtl-dir", str(rtl), "--build-dir", str(directory / "test")],
                stdout=log, stderr=subprocess.STDOUT)
        # A compile/tool failure is NOT evidence that the checker caught a bug.
        xmls = list((directory / "test" / case).glob("*.xml"))
        failures = []
        for xml in xmls:
            for test in ET.parse(xml).iter("testcase"):
                if list(test.iter("failure")):
                    failures.append(test.attrib.get("name"))
        if result.returncode == 0 or not failures:
            raise RuntimeError(f"Mutation was not caught by a running test: {name}; see {directory}")
        results.append({"mutation": name, "status": "CAUGHT", "failing_tests": failures,
                        "source_file": file, "before": before, "after": after})
        print(f"CAUGHT {name}")
    summary = {"scope": "compute core only; no DMA/ownership mutation claim",
               "results": results}
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Evidence: {work / 'summary.json'}")


if __name__ == "__main__":
    main()
