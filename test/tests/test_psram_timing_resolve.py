"""Pure-Python unit tests for ``resolve_timing_params`` profile defaults.

Runs under pytest without a simulator. Covers tb-model-04: ``ideal`` keeps
datasheet device AC and zeros only TB path placeholders.
"""

from models.psram_timing import resolve_timing_params


def test_ideal_keeps_device_ac_zeros_tb_path():
    params = resolve_timing_params("ideal")
    assert params["PSRAM_TSP_NS"] == 2.0
    assert params["PSRAM_THD_NS"] == 2.0
    assert params["PSRAM_TACLK_NS"] == 5.5
    assert params["PSRAM_TCSP_NS"] == 2.5
    assert params["PSRAM_TCHD_NS"] == 3.0
    assert params["TB_TCO_NS"] == 0.0
    assert params["TB_FLIGHT_OUT_NS"] == 0.0
    assert params["TB_FLIGHT_IN_NS"] == 0.0
    assert params["D_OUT_SIO_NS"] == 0.0
    assert params["D_IN_SIO_NS"] == 0.0
