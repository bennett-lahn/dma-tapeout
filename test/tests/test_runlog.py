"""Pure-Python unit tests for the shared REPRO / run-start helper."""

from common.runlog import (
    begin_run,
    format_repro,
    format_repro_lines,
    log_run_start,
)

def _config(**overrides) -> dict:
    base = {
        "seed": 1,
        "level": "top",
        "dut_level": "L1",
        "sim": "icarus",
        "dma_buf_depth": 5,
        "timing_profile": "ideal",
        "waves": "auto",
        "run_dir": "",
    }
    base.update(overrides)
    return base

def test_format_repro_emits_run_test_and_make_forms():
    config = _config(seed=4231, sim="verilator", timing_profile="nominal")
    lines = format_repro_lines(
        config,
        "random_legal_chain",
        module="tests.test_dma_random",
    )
    script, make = lines
    assert script.startswith(
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
    )
    assert make.startswith("REPRO: source test/env.sh && cd test && make test ")
    for line in lines:
        assert "LEVEL=top" in line
        assert "SIM=verilator" in line
        assert "SEED=4231" in line
        assert "DMA_BUF_DEPTH=5" in line
        assert "TIMING_PROFILE=nominal" in line
        assert "COCOTB_TEST_MODULES=tests.test_dma_random" in line
        assert "TEST_FILTER=random_legal_chain" in line
    assert format_repro(
        config, "random_legal_chain", module="tests.test_dma_random"
    ) == script

def test_format_repro_gl_uses_run_gl_and_gl_test():
    config = _config(level="gl", dut_level="L2")
    lines = format_repro_lines(
        config,
        "gate_same_device_smoke",
        module="tests.test_gate_level",
    )
    assert lines[0].startswith(
        "REPRO: source test/env.sh && test/scripts/run_gl.sh "
    )
    assert "make gl_test" in lines[1]
    assert "LEVEL=" not in lines[0]
    assert "SEED=1" in lines[0]
    assert "TEST_FILTER=gate_same_device_smoke" in lines[0]

def test_format_repro_appends_live_timing_env(monkeypatch):
    monkeypatch.setenv("PSRAM_TACLK_NS", "2.0")
    line = format_repro(
        _config(level="engine", timing_profile="sweep"),
        "qspi_rxedge_taclk_past_capture",
        module="tests.test_qspi_timing_launch_rx",
        extra={"PSRAM_TACLK_NS": "2.0"},
    )
    assert "TIMING_PROFILE=sweep" in line
    assert "PSRAM_TACLK_NS=2.0" in line
    assert "LEVEL=engine" in line

def test_format_repro_timing_profile_override():
    line = format_repro(
        _config(timing_profile="nominal"),
        "test_ideal_ac_and_wrap",
        module="tests.test_qspi_timing_model",
        timing_profile="ideal",
    )
    assert "TIMING_PROFILE=ideal" in line

def test_begin_run_logs_banner_then_repro_lines(monkeypatch):
    monkeypatch.setenv("SEED", "8")
    monkeypatch.setenv("LEVEL", "top")
    monkeypatch.setenv("DUT_LEVEL", "L1")
    monkeypatch.setenv("SIM", "icarus")
    monkeypatch.setenv("DMA_BUF_DEPTH", "5")
    monkeypatch.setenv("TIMING_PROFILE", "ideal")
    monkeypatch.delenv("WAVES", raising=False)

    recorded: list[tuple] = []

    class _Log:
        def info(self, fmt, *args):
            recorded.append((fmt, args))

    class _Dut:
        _log = _Log()

    config, repro = begin_run(
        _Dut(),
        "smoke_same_device_copy",
        test="TC-SMOKE",
        module="tests.test_smoke",
    )
    assert config["seed"] == 8
    assert repro.startswith("REPRO: source test/env.sh && test/scripts/run_test.sh ")
    assert "TEST_FILTER=smoke_same_device_copy" in repro
    banner = recorded[0]
    assert banner[0].startswith("RUN ")
    assert "TC-SMOKE" in banner[1][0] or "test=TC-SMOKE" in (
        banner[0] % banner[1]
    )
    assert any(
        args and str(args[0]).startswith("REPRO:")
        for fmt, args in recorded
    )

def test_log_run_start_accepts_one_string():
    recorded: list[str] = []

    class _Log:
        def info(self, fmt, *args):
            recorded.append(fmt % args if args else fmt)

    log_run_start(_Log(), _config(), "REPRO: example", test="TC-SMOKE")
    assert any("SEED=1" in line for line in recorded)
    assert any(line == "REPRO: example" for line in recorded)
