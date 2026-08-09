# dma-tapeout verification platform Makefile
# Contract: docs/llm/verification/02-platform.md
# Template shape: ttihp-verilog-template/test/Makefile
#
# Preferred agent entry (from repo root, WSL bash):
#   source test/env.sh
#   test/scripts/doctor.sh
#   test/scripts/run_smoke.sh
#   test/scripts/run_test.sh LEVEL=top SIM=icarus SEED=1
#
# Or from test/ after sourcing env.sh:
#   make doctor
#   make smoke
#   make test
#
# RTL compile order (L1): types.svh, then qspi_engine.sv, sys_controller.sv, top.v
# L0 compiles only types.svh and qspi_engine.sv plus tb_engine.
# L2 uses the gate netlist and IHP cell models instead of RTL.

SHELL               := /bin/bash

# Prefer active venv's python3; override with PYTHON=...
PYTHON              ?= python3

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SIM                 ?= icarus
LEVEL               ?= top
SEED                ?= 1
TEST_FILTER         ?=
DMA_BUF_DEPTH       ?= 1
TIMING_PROFILE      ?= ideal
WAVES               ?= auto
TOPLEVEL_LANG       ?= verilog
NETLIST             ?= gate_level_netlist.v

# TT-compatible gate selector (unset, or yes)
GATES               ?=

# Optional L2 annotation
SDF                 ?=

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PWD                 := $(CURDIR)
REPO_ROOT           := $(abspath $(CURDIR)/..)
SRC_RTL             := $(REPO_ROOT)/src/rtl
TB_DIR              := $(PWD)/tb
SCRIPTS_DIR         := $(PWD)/scripts

# ---------------------------------------------------------------------------
# LEVEL / GATES resolution (02-platform.md)
# ---------------------------------------------------------------------------

# GATES=yes with LEVEL=top selects L2 for Tiny Tapeout compatibility.
ifeq ($(GATES),yes)
  ifeq ($(LEVEL),engine)
    $(error GATES=yes is incompatible with LEVEL=engine; use LEVEL=top or LEVEL=gl)
  endif
  ifeq ($(LEVEL),top)
    override LEVEL := gl
  endif
endif

# LEVEL=gl implies gate-level flow.
ifeq ($(LEVEL),gl)
  override GATES := yes
endif

ifneq ($(filter engine top gl,$(LEVEL)),$(LEVEL))
  $(error LEVEL must be engine, top, or gl (got '$(LEVEL)'))
endif

ifneq ($(WAVES),auto)
  ifneq ($(WAVES),always)
    ifneq ($(WAVES),never)
      $(error WAVES must be auto, always, or never (got '$(WAVES)'))
    endif
  endif
endif

# Buffer-depth sweep harness: M5 wires -GDMA_BUF_DEPTH; scaffold allows 1 only.
ifeq ($(DMA_BUF_DEPTH),1)
  # ok
else ifeq ($(DMA_BUF_DEPTH),2)
  $(error DMA_BUF_DEPTH=2 blocked: sim harness sweep not wired (M5); use DMA_BUF_DEPTH=1)
else ifeq ($(DMA_BUF_DEPTH),4)
  $(error DMA_BUF_DEPTH=4 blocked: sim harness sweep not wired (M5); use DMA_BUF_DEPTH=1)
else ifeq ($(DMA_BUF_DEPTH),8)
  $(error DMA_BUF_DEPTH=8 blocked: sim harness sweep not wired (M5); use DMA_BUF_DEPTH=1)
else
  $(error DMA_BUF_DEPTH must be 1 until M5 sweep is wired (got '$(DMA_BUF_DEPTH)'))
endif

# ---------------------------------------------------------------------------
# DUT level -> HDL top (TOPLEVEL names for cocotb)
# ---------------------------------------------------------------------------

ifeq ($(LEVEL),engine)
  DUT_LEVEL           := L0
  TOPLEVEL            := tb_engine
else ifeq ($(LEVEL),top)
  DUT_LEVEL           := L1
  TOPLEVEL            := tb_top
else
  DUT_LEVEL           := L2
  TOPLEVEL            := tb_gl
endif

# ---------------------------------------------------------------------------
# Run and build isolation
# ---------------------------------------------------------------------------

RUN_DIR             ?= $(PWD)/runs/$(LEVEL)/$(SIM)/n$(DMA_BUF_DEPTH)/$(TIMING_PROFILE)/seed-$(SEED)
SIM_BUILD           ?= $(PWD)/sim_build/$(LEVEL)/$(SIM)/n$(DMA_BUF_DEPTH)/$(TIMING_PROFILE)

# ---------------------------------------------------------------------------
# Cocotb 2.x selection (not legacy COCOTB_TESTCASE / MODULE)
# ---------------------------------------------------------------------------

export SEED
export LEVEL
export DUT_LEVEL
export DMA_BUF_DEPTH
export TIMING_PROFILE
export WAVES
export RUN_DIR
export PYTHON
COCOTB_REDUCED_LOG_FMT        ?= 1
export COCOTB_REDUCED_LOG_FMT
export COCOTB_RESULTS_FILE    := $(RUN_DIR)/results.xml

ifneq ($(TEST_FILTER),)
  export COCOTB_TEST_FILTER := $(TEST_FILTER)
endif

# Default module list; smoke/directed/random targets override as needed.
COCOTB_TEST_MODULES ?= tests.test_smoke

# ---------------------------------------------------------------------------
# Simulator / compile arguments
# ---------------------------------------------------------------------------

COMPILE_ARGS        += -I$(SRC_RTL)
COMPILE_ARGS        += -I$(TB_DIR)

# Module parameter override for future depth sweep (M5). Syntax is
# simulator-specific: Verilator uses -G, Icarus uses -P<top>.<param>=<value>.
# tb_engine (L0) has no DMA_BUF_DEPTH parameter, so only pass it for L1/L2 tops.
ifneq ($(LEVEL),engine)
  ifeq ($(SIM),verilator)
    COMPILE_ARGS    += -GDMA_BUF_DEPTH=$(DMA_BUF_DEPTH)
  else
    COMPILE_ARGS    += -P$(TOPLEVEL).DMA_BUF_DEPTH=$(DMA_BUF_DEPTH)
  endif
endif

VERILOG_SOURCES     :=

ifeq ($(GATES),yes)

  SIM_BUILD         := $(PWD)/sim_build/gl/$(SIM)/n$(DMA_BUF_DEPTH)/$(TIMING_PROFILE)
  COMPILE_ARGS      += -DGL_TEST
  COMPILE_ARGS      += -DFUNCTIONAL
  COMPILE_ARGS      += -DSIM

  ifeq ($(PDK_ROOT),)
    $(error PDK_ROOT must be set for gate-level (GATES=yes / LEVEL=gl) simulation)
  endif

  VERILOG_SOURCES   += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
  VERILOG_SOURCES   += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v
  VERILOG_SOURCES   += $(PWD)/$(NETLIST)

  ifeq ($(wildcard $(PWD)/$(NETLIST)),)
    $(warning NETLIST $(PWD)/$(NETLIST) not found; L2 runs are blocked until netlist exists)
  endif

  ifneq ($(SDF),)
    # Simulator-specific SDF wiring deferred to M6 (09-gate-level-and-x.md).
    COMPILE_ARGS    += -DSDF_ANNOTATE=\"$(SDF)\"
  endif

  VERILOG_SOURCES   += $(TB_DIR)/tb_gl.sv

else ifeq ($(LEVEL),engine)

  # L0: package + engine only (types.svh must precede qspi_engine.sv).
  VERILOG_SOURCES   += $(SRC_RTL)/types.svh
  VERILOG_SOURCES   += $(SRC_RTL)/qspi_engine.sv
  VERILOG_SOURCES   += $(TB_DIR)/tb_engine.sv

else

  # L1: integrated RTL set in documented order.
  VERILOG_SOURCES   += $(SRC_RTL)/types.svh
  VERILOG_SOURCES   += $(SRC_RTL)/qspi_engine.sv
  VERILOG_SOURCES   += $(SRC_RTL)/sys_controller.sv
  VERILOG_SOURCES   += $(SRC_RTL)/top.v
  VERILOG_SOURCES   += $(TB_DIR)/tb_top.sv

endif

ifeq ($(WAVES),never)
  COMPILE_ARGS      += -DWAVES_DISABLE
endif

ifeq ($(SIM),icarus)
  # FST is a runtime vvp flag (enables the FST dump backend for tb_top's own
  # $dumpfile/$dumpvars), not a compile-time iverilog argument.
  FST               := -fst
else ifeq ($(SIM),verilator)
  # Verilator 5.051 (OSS CAD Suite) timing support for HDL delays and cocotb clocks.
  COMPILE_ARGS      += --timing
  # X-propagation settings recorded per-run in M6; not defaulted here.
else
  $(warning SIM=$(SIM) is not in the primary matrix (icarus, verilator))
endif

# ---------------------------------------------------------------------------
# Pre-run hooks
# ---------------------------------------------------------------------------

.PHONY: _ensure_run_dir _print_config _print_versions _print_repro sim_prep doctor

_ensure_run_dir:
	@mkdir -p "$(RUN_DIR)"

_print_config:
	@echo "=== dma-tapeout verification ==="
	@echo "LEVEL=$(LEVEL) ($(DUT_LEVEL))  TOPLEVEL=$(TOPLEVEL)  SIM=$(SIM)"
	@echo "SEED=$(SEED)  DMA_BUF_DEPTH=$(DMA_BUF_DEPTH)  TIMING_PROFILE=$(TIMING_PROFILE)"
	@echo "WAVES=$(WAVES)  GATES=$(GATES)  TEST_FILTER=$(TEST_FILTER)"
	@echo "RUN_DIR=$(RUN_DIR)"
	@echo "SIM_BUILD=$(SIM_BUILD)"
	@echo "COCOTB_TEST_MODULES=$(COCOTB_TEST_MODULES)"
	@echo "PYTHON=$(PYTHON)  VIRTUAL_ENV=$${VIRTUAL_ENV:-<unset>}"
	@$(MAKE) -s _print_versions

_print_versions:
	@echo "--- toolchain versions (capture for CI/sign-off) ---"
	@$(PYTHON) --version 2>/dev/null || echo "$(PYTHON): not found"
	@$(PYTHON) -m pip freeze 2>/dev/null | sed -n '1,5p' || true
	@iverilog -V 2>/dev/null | sed -n '1p' || echo "iverilog: not found"
	@verilator --version 2>/dev/null || echo "verilator: not found"
	@yosys -V 2>/dev/null | sed -n '1p' || echo "yosys: not found"
	@sby --version 2>/dev/null || echo "sby: not found"

_print_repro:
	@echo "REPRO: source $(REPO_ROOT)/test/env.sh && cd $(PWD) && make test LEVEL=$(LEVEL) SIM=$(SIM) SEED=$(SEED) DMA_BUF_DEPTH=$(DMA_BUF_DEPTH) TIMING_PROFILE=$(TIMING_PROFILE) TEST_FILTER=$(TEST_FILTER) WAVES=$(WAVES)"
	@echo "REPRO: source $(REPO_ROOT)/test/env.sh && $(SCRIPTS_DIR)/run_test.sh LEVEL=$(LEVEL) SIM=$(SIM) SEED=$(SEED) DMA_BUF_DEPTH=$(DMA_BUF_DEPTH) TIMING_PROFILE=$(TIMING_PROFILE) TEST_FILTER=$(TEST_FILTER) WAVES=$(WAVES)"

sim_prep: _ensure_run_dir _print_config

doctor:
	@bash "$(SCRIPTS_DIR)/doctor.sh"

# ---------------------------------------------------------------------------
# Primary targets (02-platform.md)
# ---------------------------------------------------------------------------

.PHONY: default test smoke directed random regression formal waves

default: test

test: sim_prep
	@$(MAKE) sim 2>&1 | tee "$(RUN_DIR)/run.log"; \
	 exit=$${PIPESTATUS[0]}; \
	 if [ -f "$(PWD)/dump.fst" ]; then mv -f "$(PWD)/dump.fst" "$(RUN_DIR)/dump.fst"; fi; \
	 if [ -f "$(PWD)/results.xml" ] && [ "$(PWD)/results.xml" != "$(RUN_DIR)/results.xml" ]; then \
	   cp -f "$(PWD)/results.xml" "$(RUN_DIR)/results.xml"; \
	 fi; \
	 $(MAKE) -s _print_repro; \
	 exit $$exit

smoke:
	$(MAKE) test LEVEL=top SIM=icarus SEED=1 \
		COCOTB_TEST_MODULES=tests.test_smoke \
		TEST_FILTER=smoke

# M2 directed cases in tests.test_dma_directed (exclude M5 dma_buf_depth_sweep).
# Keep parentheses quoted on the recipe line; bare ( breaks bash before make sees it.
DIRECTED_TEST_FILTER ?= ^(tcd_big_endian_flags|same_device_psram0|same_device_psram1|cross_device_0_to_1|cross_device_1_to_0|multi_tcd_chain|next_device_alternate|transfer_length_corners|quit_descriptor_priority|empty_chain_at_head|restart_after_completion|wide_address_space|overlapping_same_device_ranges)$$

directed:
	$(MAKE) test \
		COCOTB_TEST_MODULES=tests.test_smoke,tests.test_qspi,tests.test_dma_directed,tests.test_reset_and_bus \
		TEST_FILTER="$(if $(TEST_FILTER),$(TEST_FILTER),$(DIRECTED_TEST_FILTER))"

random:
	$(MAKE) test \
		COCOTB_TEST_MODULES=tests.test_dma_random \
		TEST_FILTER=$(if $(TEST_FILTER),$(TEST_FILTER),random)

regression:
	@echo "TODO(M2+): run configured seed list and simulator matrix (see 02-platform.md CI matrix)"
	@exit 1

formal:
	@echo "TODO(M4): SymbiYosys jobs under test/formal/ (see docs/llm/verification/07-formal.md)"
	@exit 1

waves:
	@if [ -f "$(RUN_DIR)/dump.fst" ]; then \
		echo "Waveform: $(RUN_DIR)/dump.fst"; \
		echo "Open with: gtkwave $(RUN_DIR)/dump.fst"; \
	elif [ -f "$(RUN_DIR)/dump.vcd" ]; then \
		echo "Waveform: $(RUN_DIR)/dump.vcd"; \
	else \
		echo "No waveform in RUN_DIR=$(RUN_DIR)"; \
		echo "Re-run with WAVES=always or inspect a failing run directory."; \
		exit 1; \
	fi

# ---------------------------------------------------------------------------
# Cocotb simulator rules (require active dma-venv / cocotb-config on PATH)
# ---------------------------------------------------------------------------

COCOTB_MAKEFILES := $(shell command -v cocotb-config >/dev/null 2>&1 && cocotb-config --makefiles)

ifeq ($(COCOTB_MAKEFILES),)
  .PHONY: sim
  sim:
	@echo "cocotb-config not found on PATH."
	@echo "From repo root: source test/env.sh && cd test && make <target>"
	@echo "Or use:         test/scripts/run_smoke.sh / test/scripts/run_test.sh"
	@exit 1

  clean:
	rm -rf "$(PWD)/sim_build"
	@find "$(PWD)/runs" -mindepth 1 ! -name '.gitkeep' -delete 2>/dev/null || true
else
  include $(COCOTB_MAKEFILES)/Makefile.sim

  # Extend cocotb clean (Makefile.sim defines clean::).
  clean::
	rm -rf "$(PWD)/sim_build"
	@find "$(PWD)/runs" -mindepth 1 ! -name '.gitkeep' -delete 2>/dev/null || true
endif
