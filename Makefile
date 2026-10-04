PYTHON ?= python3
VIVADO ?= vivado
MANIFEST ?= build/gemm/build.json
PORT ?=
GEMM_P ?= 4
FORMAL_BUILD ?= build/formal_dma_rows
FORMAL_BUFFERS_BUILD ?= build/formal_buffers
BOARD ?= nexys_a7_50t
RELEASE_BUILD ?= build/gemm_release
RELEASE_P ?= 8
RELEASE_T ?= 32
RELEASE_BAUD ?= 1000000
RELEASE_READ_SLOTS ?= 4
RELEASE_MODE ?= 1
RELEASE_OUTPUT ?= build/release_measurements

.PHONY: formal
.PHONY: bitstream bench hw-release demo test-release test-formal-traces

# Full-system entry points use the selectable scheduler. Older module and
# serial targets below retain their original defaults and evidence boundaries.
bitstream:
	$(if $(filter nexys_a7_50t,$(BOARD)),,$(error BOARD must be nexys_a7_50t))
	"$(PYTHON)" scripts/build_ddr_gemm.py --stage sim --overlap --p "$(RELEASE_P)" --t "$(RELEASE_T)" --read-slots "$(RELEASE_READ_SLOTS)" --baud "$(RELEASE_BAUD)" --build-dir "$(RELEASE_BUILD)" --vivado "$(VIVADO)"
	"$(PYTHON)" scripts/build_ddr_gemm.py --stage bitstream --overlap --p "$(RELEASE_P)" --t "$(RELEASE_T)" --read-slots "$(RELEASE_READ_SLOTS)" --baud "$(RELEASE_BAUD)" --build-dir "$(RELEASE_BUILD)" --vivado "$(VIVADO)"
bench:
	$(if $(strip $(PORT)),,$(error Set PORT and MANIFEST for the already programmed qualified image))
	"$(PYTHON)" scripts/keep_awake.py -- "$(PYTHON)" scripts/qualify_release.py --phase benchmark --modes both --port "$(PORT)" --manifest "$(MANIFEST)" --output "$(RELEASE_OUTPUT)"
hw-release:
	$(if $(strip $(PORT)),,$(error Set PORT and MANIFEST for the already programmed qualified image))
	"$(PYTHON)" scripts/keep_awake.py -- "$(PYTHON)" scripts/qualify_release.py --phase all --modes both --port "$(PORT)" --manifest "$(MANIFEST)" --output "$(RELEASE_OUTPUT)"
demo:
	$(if $(strip $(PORT)),,$(error Set PORT and MANIFEST for the already programmed qualified image))
	"$(PYTHON)" -m host.gemm --port "$(PORT)" --manifest "$(MANIFEST)" --mode "$(RELEASE_MODE)" --m 5 --n 3 --k 9 --repeats 1 --retries 0 --output "$(RELEASE_OUTPUT)"
test-release:
	"$(PYTHON)" -m unittest discover -s tb -p test_qualify_release.py
	"$(PYTHON)" -m unittest discover -s tb -p test_collect_benchmarks.py
test-formal-traces:
	"$(PYTHON)" -m unittest discover -s tb -p test_formal_buffers.py

.PHONY: lint test test-memory test-tile test-tile-overlap test-preview test-axi test-dma test-tile-dma test-tile-dma-duplex test-tile-scheduler test-ddr-overlap-job test-ddr-overlap-core test-ddr-job test-ddr-registers test-ddr-core test-ddr-diag test-vendor-reference profile-ddr profile-read-dma sim-vendor sim-ddr sim-gemm-ddr ddr-diag-bitstream ddr-gemm-bitstream hw-test mutation mutation-read-dma mutation-tile-overlap synth-core synth-memory synth-tile preview-bitstream help
lint:
	"$(PYTHON)" scripts/lint.py
test:
	"$(PYTHON)" scripts/test.py
	$(MAKE) test-release test-formal-traces PYTHON="$(PYTHON)"
	"$(PYTHON)" scripts/test_memory.py
	"$(PYTHON)" scripts/test_tile.py
	$(MAKE) test-tile-overlap PYTHON="$(PYTHON)"
	$(MAKE) test-preview PYTHON="$(PYTHON)"
	$(MAKE) test-axi PYTHON="$(PYTHON)"
	$(MAKE) test-dma PYTHON="$(PYTHON)"
	$(MAKE) test-tile-dma PYTHON="$(PYTHON)"
	$(MAKE) test-tile-dma-duplex PYTHON="$(PYTHON)"
	$(MAKE) test-tile-scheduler PYTHON="$(PYTHON)"
	$(MAKE) test-ddr-overlap-job PYTHON="$(PYTHON)"
	$(MAKE) test-ddr-overlap-core PYTHON="$(PYTHON)"
	$(MAKE) test-ddr-job PYTHON="$(PYTHON)"
	$(MAKE) test-ddr-registers PYTHON="$(PYTHON)"
	$(MAKE) test-ddr-core PYTHON="$(PYTHON)"
	$(MAKE) test-ddr-diag PYTHON="$(PYTHON)"
test-memory:
	"$(PYTHON)" scripts/test_memory.py
test-tile:
	"$(PYTHON)" scripts/test_tile.py
test-tile-overlap:
	"$(PYTHON)" scripts/test_tile_overlap.py
test-preview:
	"$(PYTHON)" -m unittest discover -s tb -p test_host_preview.py
	"$(PYTHON)" scripts/test_packet_transport.py
	"$(PYTHON)" scripts/test_preview_controller.py
	"$(PYTHON)" scripts/test_preview_uart.py
test-axi:
	"$(PYTHON)" scripts/test_axi_burst.py
test-dma:
	"$(PYTHON)" scripts/test_dma_rows.py
formal:
	"$(PYTHON)" scripts/formal_dma_rows.py --build-dir "$(FORMAL_BUILD)"
	"$(PYTHON)" scripts/formal_buffers.py --build-dir "$(FORMAL_BUFFERS_BUILD)"
test-tile-dma:
	"$(PYTHON)" scripts/test_tile_dma.py
test-tile-dma-duplex:
	"$(PYTHON)" scripts/test_tile_dma_duplex.py
test-tile-scheduler:
	"$(PYTHON)" scripts/test_tile_scheduler.py
test-ddr-overlap-job:
	"$(PYTHON)" scripts/test_ddr_overlap_job.py
test-ddr-overlap-core:
	"$(PYTHON)" scripts/test_ddr_core.py --overlap --build-dir build/test_ddr_core_overlap_compatibility
	"$(PYTHON)" scripts/test_ddr_core.py --overlap --suite overlap --build-dir build/test_ddr_core_overlap
test-ddr-job:
	"$(PYTHON)" scripts/test_ddr_job.py
test-ddr-registers:
	"$(PYTHON)" scripts/test_ddr_registers.py
test-ddr-core:
	"$(PYTHON)" -m unittest discover -s tb -p test_host_gemm.py
	"$(PYTHON)" -m unittest discover -s tb -p test_build_ddr_gemm.py
	"$(PYTHON)" -m unittest discover -s tb -p test_program_ddr_gemm.py
	"$(PYTHON)" -m unittest discover -s tb -p test_hw_ddr_gemm.py
	$(MAKE) test-vendor-reference PYTHON="$(PYTHON)"
	"$(PYTHON)" scripts/test_ddr_core.py
test-vendor-reference:
	"$(PYTHON)" scripts/test_vendor_reference.py
profile-ddr:
	"$(PYTHON)" scripts/profile_ddr.py
profile-read-dma:
	"$(PYTHON)" scripts/profile_read_dma.py
test-ddr-diag:
	"$(PYTHON)" scripts/test_ddr_diag.py
	"$(PYTHON)" scripts/test_ddr_diag_control.py
	"$(PYTHON)" -m unittest discover -s tb -p test_host_ddr_diag.py
	"$(PYTHON)" -m unittest discover -s tb -p test_build_ddr_diag.py
sim-vendor:
	"$(PYTHON)" scripts/test_mig.py
sim-ddr:
	"$(PYTHON)" scripts/build_ddr_diag.py --stage sim --vivado "$(VIVADO)"
ddr-diag-bitstream:
	"$(PYTHON)" scripts/build_ddr_diag.py --stage bitstream --vivado "$(VIVADO)"
sim-gemm-ddr:
	"$(PYTHON)" scripts/build_ddr_gemm.py --stage sim --vivado "$(VIVADO)"
ddr-gemm-bitstream:
	"$(PYTHON)" scripts/build_ddr_gemm.py --stage bitstream --vivado "$(VIVADO)"
hw-test:
	$(if $(strip $(PORT)),,$(error Set PORT and MANIFEST for the programmed serial DDR image))
	"$(PYTHON)" scripts/hw_test_ddr_gemm.py --p "$(GEMM_P)" --port "$(PORT)" --manifest "$(MANIFEST)"
mutation:
	"$(PYTHON)" scripts/mutation_test.py
mutation-read-dma:
	"$(PYTHON)" scripts/mutation_read_queue.py
mutation-tile-overlap:
	"$(PYTHON)" scripts/mutation_tile_overlap.py
synth-core:
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_core.tcl
synth-memory:
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_memory.tcl
synth-tile:
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_tile.tcl -tclargs 4
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_tile.tcl -tclargs 8
preview-bitstream:
	"$(PYTHON)" scripts/build_preview.py --vivado "$(VIVADO)"
help:
	@echo "Full system: bitstream BOARD=nexys_a7_50t RELEASE_BUILD=build/fresh_name (P8/T32, READ4, selectable modes, 1 Mbaud)."
	@echo "Programmed image: demo, bench, hw-release PORT=... MANIFEST=... RELEASE_OUTPUT=build/fresh_results. These targets never reset or program the board."
	@echo "Release tooling: test-release test-formal-traces (software fixtures; no board or plotting dependencies)."
	@echo "Checks: lint, test, test-memory, test-tile, test-tile-overlap, test-preview, test-axi, test-dma, test-tile-dma, test-tile-dma-duplex, test-tile-scheduler, test-ddr-job, test-ddr-registers, test-ddr-core, test-ddr-diag, mutation. See docs/testing.md."
	@echo "Scoped formal checks: formal PYTHON=... FORMAL_BUILD=build/fresh_rows FORMAL_BUFFERS_BUILD=build/fresh_buffers (optional requirements-formal.txt; see formal/README.md)."
	@echo "Vendor simulation: sim-vendor (Vivado and generated MIG required)."
	@echo "Integrated DDR diagnostic: sim-ddr, then ddr-diag-bitstream (native Python and Vivado)."
	@echo "Serial DDR GEMM: sim-gemm-ddr, then ddr-gemm-bitstream (native Python and Vivado)."
	@echo "Serial board qualification: hw-test GEMM_P=4 or 8 PORT=... MANIFEST=... (qualified image required)."
	@echo "Serial P4 cycle attribution: profile-ddr (behavioral AXI models; no physical DDR timing claim)."
	@echo "P8 read-depth comparison: profile-read-dma (behavioral helper sequence; no board performance claim)."
	@echo "Local concurrent-port defect checks: mutation-tile-overlap (isolated copies; no DDR overlap claim)."
	@echo "Vivado: synth-core, synth-memory, synth-tile, preview-bitstream."
