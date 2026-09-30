PYTHON ?= python3
VIVADO ?= vivado

.PHONY: lint test test-memory test-tile mutation synth-core synth-memory synth-tile help
lint:
	"$(PYTHON)" scripts/lint.py
test:
	"$(PYTHON)" scripts/test.py
	"$(PYTHON)" scripts/test_memory.py
	"$(PYTHON)" scripts/test_tile.py
test-memory:
	"$(PYTHON)" scripts/test_memory.py
test-tile:
	"$(PYTHON)" scripts/test_tile.py
mutation:
	"$(PYTHON)" scripts/mutation_test.py
synth-core:
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_core.tcl
synth-memory:
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_memory.tcl
synth-tile:
	"$(VIVADO)" -mode batch -notrace -source scripts/synth_tile.tcl
help:
	@echo "Checks: lint, test, test-memory, test-tile, mutation. See docs/testing.md."
	@echo "Vivado: synth-core, synth-memory, synth-tile."
