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

preview = sorted((root / 'rtl').rglob('*.sv')) + [
    root / 'accelerator nexys.srcs/sources_1/new' / name
    for name in ('uart_rx.sv', 'uart_tx.sv')]
subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_preview_uart',
                *map(str, preview)], check=True)
print('PASS: complete preview UART compile/warnings')

for read_slots in (1, 4):
    subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_axi_burst',
                    f'-Pgemm_axi_burst.READ_SLOTS={read_slots}',
                    str(root / 'rtl/memory/gemm_axi_burst.sv')], check=True)
    print(f'PASS: AXI burst primitive compile/warnings READ_SLOTS={read_slots}')
for read_slots in (1, 4):
    subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_dma_rows',
                    f'-Pgemm_dma_rows.READ_SLOTS={read_slots}',
                    str(root / 'rtl/memory/gemm_dma_rows.sv')], check=True)
    print(f'PASS: DMA row sequencer compile/warnings READ_SLOTS={read_slots}')
for p in (4, 8):
    for t in (8, 32):
        for read_slots in (1, 4):
            subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_tile_dma',
                            f'-Pgemm_tile_dma.P={p}', f'-Pgemm_tile_dma.T={t}',
                            f'-Pgemm_tile_dma.READ_SLOTS={read_slots}',
                            str(root / 'rtl/memory/gemm_dma_rows.sv'),
                            str(root / 'rtl/memory/gemm_tile_dma_read_queue.sv'),
                            str(root / 'rtl/memory/gemm_tile_dma.sv')], check=True)
            print(f'PASS: tile DMA adapter compile/warnings P={p} T={t} READ_SLOTS={read_slots}')
            subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_tile_dma_duplex',
                            f'-Pgemm_tile_dma_duplex.P={p}', f'-Pgemm_tile_dma_duplex.T={t}',
                            f'-Pgemm_tile_dma_duplex.READ_SLOTS={read_slots}',
                            str(root / 'rtl/memory/gemm_dma_rows.sv'),
                            str(root / 'rtl/memory/gemm_tile_dma_read_queue.sv'),
                            str(root / 'rtl/memory/gemm_tile_dma.sv'),
                            str(root / 'rtl/memory/gemm_tile_dma_duplex.sv')], check=True)
            print(f'PASS: duplex tile DMA compile/warnings P={p} T={t} READ_SLOTS={read_slots}')
        subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_ddr_job',
                        f'-Pgemm_ddr_job.P={p}', f'-Pgemm_ddr_job.T={t}',
                        str(root / 'rtl/control/gemm_ddr_job.sv')], check=True)
        print(f'PASS: DDR job controller compile/warnings P={p} T={t}')
        subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_tile_scheduler',
                        f'-Pgemm_tile_scheduler.P={p}', f'-Pgemm_tile_scheduler.T={t}',
                        str(root / 'rtl/control/gemm_tile_scheduler.sv')], check=True)
        print(f'PASS: tagged tile scheduler compile/warnings P={p} T={t}')
        subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_ddr_overlap_job',
                        f'-Pgemm_ddr_overlap_job.P={p}', f'-Pgemm_ddr_overlap_job.T={t}',
                        str(root / 'rtl/control/gemm_tile_scheduler.sv'),
                        str(root / 'rtl/control/gemm_ddr_overlap_job.sv')], check=True)
        print(f'PASS: overlap DDR job shell compile/warnings P={p} T={t}')
        subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_ddr_registers',
                        f'-Pgemm_ddr_registers.P={p}', f'-Pgemm_ddr_registers.T={t}',
                        str(root / 'rtl/control/gemm_ddr_registers.sv')], check=True)
        print(f'PASS: DDR register interface compile/warnings P={p} T={t}')
        for read_slots in (1, 4):
            for overlap in (0, 1):
                subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', 'gemm_ddr_core',
                                f'-Pgemm_ddr_core.P={p}', f'-Pgemm_ddr_core.T={t}',
                                f'-Pgemm_ddr_core.READ_SLOTS={read_slots}',
                                f'-Pgemm_ddr_core.ENABLE_OVERLAP={overlap}',
                                *map(str, sorted((root / 'rtl').rglob('*.sv')))], check=True)
                print(f'PASS: complete DDR command/memory core compile/warnings P={p} T={t} READ_SLOTS={read_slots} ENABLE_OVERLAP={overlap}')
for top in ('gemm_ddr_diag', 'gemm_ddr_diag_control'):
    subprocess.run(['iverilog', '-g2012', '-Wall', '-t', 'null', '-s', top,
                    str(root / 'rtl/control' / f'{top}.sv')], check=True)
print('PASS: DDR diagnostic and packet controller compile/warnings')
