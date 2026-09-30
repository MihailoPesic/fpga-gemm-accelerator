"""Compile both public core configurations with Icarus warnings enabled."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
sources = sorted((root / "rtl/core").glob("*.sv"))
for p in (4, 8):
    subprocess.run(["iverilog", "-g2012", "-Wall", "-t", "null", "-s", "gemm_microtile",
                    f"-Pgemm_microtile.P={p}", *map(str, sources)], check=True)
    print(f"PASS: Icarus compile/warnings P={p}")
    for t in (8, 32):
        memory = sorted((root / "rtl/memory").glob("*.sv"))
        subprocess.run(["iverilog", "-g2012", "-Wall", "-t", "null", "-s", "gemm_bram_microtile",
                        f"-Pgemm_bram_microtile.P={p}", f"-Pgemm_bram_microtile.T={t}",
                        *map(str, sources + memory)], check=True)
        print(f"PASS: banked Icarus compile/warnings P={p} T={t}")
        control = sorted((root / "rtl/control").glob("*.sv"))
        subprocess.run(["iverilog", "-g2012", "-Wall", "-t", "null", "-s", "gemm_tile_engine",
                        f"-Pgemm_tile_engine.P={p}", f"-Pgemm_tile_engine.T={t}",
                        *map(str, sources + memory + control)], check=True)
        print(f"PASS: tile engine compile/warnings P={p} T={t}")
