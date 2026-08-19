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
# Package ceiling from src/types.svh (D20). Tapeout default matches RTL N=5.
DMA_BUF_DEPTH_MAX   ?= 8
DMA_BUF_DEPTH       ?= 5
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
SRC_RTL             := $(REPO_ROOT)/src
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

# L2 default: keep waves (02-platform.md). Caller may still set never/always.
ifeq ($(LEVEL),gl)
  ifeq ($(WAVES),auto)
    override WAVES := always
  endif
endif

# Accept any integer in 1..DMA_BUF_DEPTH_MAX (not restricted to powers of two).
_DMA_BUF_DEPTH_OK := $(shell \
  echo '$(DMA_BUF_DEPTH)' | grep -Eq '^[0-9]+$$' \
  && [ '$(DMA_BUF_DEPTH)' -ge 1 ] \
  && [ '$(DMA_BUF_DEPTH)' -le $(DMA_BUF_DEPTH_MAX) ] \
  && echo ok)
ifeq ($(_DMA_BUF_DEPTH_OK),)
  $(error DMA_BUF_DEPTH must be an integer in 1..$(DMA_BUF_DEPTH_MAX) (got '$(DMA_BUF_DEPTH)'))
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
export DMA_BUF_DEPTH_MAX
export TIMING_PROFILE
export WAVES
export RUN_DIR
export PYTHON
COCOTB_REDUCED_LOG_FMT        ?= 1
export COCOTB_REDUCED_LOG_FMT
export COCOTB_RESULTS_FILE    := $(RUN_DIR)/results.xml

ifneq ($(TEST_FILTER),)
  export COCOTB_TEST_FILTER := '$(TEST_FILTER)'
endif
ifeq ($(TEST_FILTER),)
  unexport COCOTB_TEST_FILTER
endif

# Default module list; smoke/directed/random/gl_test targets override as needed.
# GATES=yes / LEVEL=gl runs the L2 directed subset, not full random.
ifeq ($(LEVEL),gl)
  COCOTB_TEST_MODULES ?= tests.test_gate_level
else
  COCOTB_TEST_MODULES ?= tests.test_smoke
endif

# ---------------------------------------------------------------------------
# Simulator / compile arguments
# ---------------------------------------------------------------------------

COMPILE_ARGS        += -I$(SRC_RTL)
COMPILE_ARGS        += -I$(TB_DIR)

# Module parameter override for depth sweep (M5). Syntax is
# simulator-specific: Verilator uses -G, Icarus uses -P<top>.<param>=<value>.
# tb_engine (L0) has no DMA_BUF_DEPTH parameter.
# L2 uses a flattened N=5 netlist: do not pass -Ptb_gl.DMA_BUF_DEPTH (that
# does not resynthesize the gate DUT). Python still sees DMA_BUF_DEPTH=5.
ifeq ($(LEVEL),gl)
  ifneq ($(DMA_BUF_DEPTH),5)
    $(error L2 netlist is elaborated at DMA_BUF_DEPTH=5; got $(DMA_BUF_DEPTH). Passing another depth does not resynthesize the gate DUT)
  endif
else ifneq ($(LEVEL),engine)
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
    $(error NETLIST $(PWD)/$(NETLIST) not found; copy the N=5 unpowered nl view via test/scripts/run_gl.sh. L2 is blocked, not a pass)
  endif

  export NETLIST
  NETLIST_SHA256    := $(shell sha256sum "$(PWD)/$(NETLIST)" 2>/dev/null | cut -d" " -f1)
  export NETLIST_SHA256
  export PDK_ROOT

  ifneq ($(SDF),)
    # SDF remains blocked until a qualified annotation run exists.
    # Defining this does not make a zero-delay GL run an SDF pass.
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
	@if [ "$(LEVEL)" = gl ]; then \
	  echo "NETLIST=$(NETLIST) SHA256=$${NETLIST_SHA256:-<missing>}"; \
	  echo "PDK_ROOT=$(PDK_ROOT)"; \
	  echo "SDF=$(if $(SDF),$(SDF),<unset>) (zero-delay functional GL is not an SDF pass; SDF remains blocked unless qualified)"; \
	fi
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

.PHONY: default test smoke directed depth random regression formal waves gl_test

# Helper rules (_ensure_run_dir, etc.) appear earlier in this file. Without an
# explicit default goal, GNU make would run the first of those and succeed
# without simulating. Tiny Tapeout gl_test then does `GATES=yes make` and
# greps test/results.xml.
.DEFAULT_GOAL := test
default: test

test: sim_prep
	@$(MAKE) sim 2>&1 | tee "$(RUN_DIR)/run.log"; \
	 exit=$${PIPESTATUS[0]}; \
	 if [ -f "$(PWD)/dump.fst" ]; then mv -f "$(PWD)/dump.fst" "$(RUN_DIR)/dump.fst"; fi; \
	 if [ -f "$(PWD)/dump.vcd" ]; then mv -f "$(PWD)/dump.vcd" "$(RUN_DIR)/dump.vcd"; fi; \
	 if [ -f "$(RUN_DIR)/dump.fst" ]; then cp -f "$(RUN_DIR)/dump.fst" "$(PWD)/tb.fst"; fi; \
	 if [ -f "$(RUN_DIR)/dump.vcd" ]; then cp -f "$(RUN_DIR)/dump.vcd" "$(PWD)/tb.vcd"; fi; \
	 if [ -f "$(RUN_DIR)/results.xml" ]; then \
	   cp -f "$(RUN_DIR)/results.xml" "$(PWD)/results.xml"; \
	 elif [ -f "$(PWD)/results.xml" ] && [ "$(PWD)/results.xml" != "$(RUN_DIR)/results.xml" ]; then \
	   cp -f "$(PWD)/results.xml" "$(RUN_DIR)/results.xml"; \
	 fi; \
	 $(MAKE) -s _print_repro; \
	 exit $$exit

smoke:
	$(MAKE) test LEVEL=top SIM=icarus SEED=1 \
		COCOTB_TEST_MODULES=tests.test_smoke \
		TEST_FILTER=smoke

# TT GATES=yes adapter: L2 directed subset at flattened N=5, TIMING_PROFILE=ideal.
# Official Tiny Tapeout action greps test/results.xml (copied after every test).
gl_test:
	$(MAKE) test LEVEL=gl GATES=yes SIM=icarus \
		DMA_BUF_DEPTH=5 TIMING_PROFILE=ideal \
		COCOTB_TEST_MODULES=tests.test_gate_level \
		RUN_DIR="$(PWD)/runs/gl/icarus/n5/ideal/seed-$(SEED)" \
		SIM_BUILD="$(PWD)/sim_build/gl/icarus/n5/ideal"

# M2 directed cases in tests.test_dma_directed.
# COV-DEPTH filled by make depth.
# Run the whole module; cocotb filters fullname module.name.
directed:
	$(MAKE) test \
		COCOTB_TEST_MODULES=tests.test_dma_directed \
		TEST_FILTER=$(if $(TEST_FILTER),$(TEST_FILTER),)

# TC-DEPTH harness: isolated directed build per depth in 1..DMA_BUF_DEPTH_MAX.
# Compile-time parameter; not a single cocotb process that recompiles.
# Pass RUN_DIR/SIM_BUILD explicitly: parent exports RUN_DIR for the default
# depth, and `?=` would otherwise keep that sticky path across the loop.
depth:
	@set -euo pipefail; \
	 for n in $$(seq 1 $(DMA_BUF_DEPTH_MAX)); do \
	   echo "=== TC-DEPTH: DMA_BUF_DEPTH=$$n / $(DMA_BUF_DEPTH_MAX) ==="; \
	   $(MAKE) directed DMA_BUF_DEPTH=$$n \
	     RUN_DIR="$(PWD)/runs/$(LEVEL)/$(SIM)/n$$n/$(TIMING_PROFILE)/seed-$(SEED)" \
	     SIM_BUILD="$(PWD)/sim_build/$(LEVEL)/$(SIM)/n$$n/$(TIMING_PROFILE)"; \
	 done; \
	 echo "TC-DEPTH: all depths 1..$(DMA_BUF_DEPTH_MAX) passed"

# One-seed constrained-random legal chain (tests.test_dma_random).
# Tapeout default DMA_BUF_DEPTH=5 and TIMING_PROFILE=ideal come from the
# Makefile defaults above; do not override them here.
RANDOM_TEST_FILTER ?= random_legal_chain

random:
	$(MAKE) test \
		COCOTB_TEST_MODULES=tests.test_dma_random \
		TEST_FILTER=$(if $(TEST_FILTER),$(TEST_FILTER),$(RANDOM_TEST_FILTER))

# Representative seed list (Icarus authoritative). Override with
# REGRESSION_SEEDS="1 2 3" REGRESSION_SIMS="icarus verilator".
REGRESSION_SEEDS ?= 1 2 3 5 8
REGRESSION_SIMS  ?= icarus

regression:
	@set -euo pipefail; \
	 for sim in $(REGRESSION_SIMS); do \
	   for seed in $(REGRESSION_SEEDS); do \
	     echo "=== regression SIM=$$sim SEED=$$seed DMA_BUF_DEPTH=$(DMA_BUF_DEPTH) TIMING_PROFILE=$(TIMING_PROFILE) ==="; \
	     $(MAKE) random SIM=$$sim SEED=$$seed \
	       RUN_DIR="$(PWD)/runs/$(LEVEL)/$$sim/n$(DMA_BUF_DEPTH)/$(TIMING_PROFILE)/seed-$$seed" \
	       SIM_BUILD="$(PWD)/sim_build/$(LEVEL)/$$sim/n$(DMA_BUF_DEPTH)/$(TIMING_PROFILE)"; \
	   done; \
	 done; \
	 echo "regression: $(REGRESSION_SIMS) x $(REGRESSION_SEEDS) passed"

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
