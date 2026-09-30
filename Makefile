PYTHON ?= python3
VIVADO ?= vivado

.PHONY: lint test test-memory test-tile test-preview mutation synth-core synth-memory synth-tile preview-bitstream help
lint:
	"$(PYTHON)" scripts/lint.py
test:
	"$(PYTHON)" scripts/test.py
	"$(PYTHON)" scripts/test_memory.py
	"$(PYTHON)" scripts/test_tile.py
	$(MAKE) test-preview PYTHON="$(PYTHON)"
test-memory:
	"$(PYTHON)" scripts/test_memory.py
test-tile:
	"$(PYTHON)" scripts/test_tile.py
test-preview:
	"$(PYTHON)" -m unittest discover -s tb -p test_host_preview.py
	"$(PYTHON)" scripts/test_packet_transport.py
	"$(PYTHON)" scripts/test_preview_controller.py
	"$(PYTHON)" scripts/test_preview_uart.py
mutation:
	"$(PYTHON)" scripts/mutation_test.py
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
	@echo "Checks: lint, test, test-memory, test-tile, test-preview, mutation. See docs/testing.md."
	@echo "Vivado: synth-core, synth-memory, synth-tile, preview-bitstream."
