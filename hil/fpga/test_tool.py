"""Unit tests for the FPGA bitstream helper (no Yosys, no demoboard)."""

import os

import pytest

from hil.fpga import tool
from hil.fpga.__main__ import build_parser, main


INFO_YAML = """\
project:
  title: "TinyDMA"
  clock_hz:     66000000
  top_module:  "tt_um_lahnb_sgdma"
  source_files:
    - "top.v"
    - "types.svh"
    - "qspi_engine.sv"
    - "sys_controller.sv"
"""


def test_selftest_parse_info_yaml_reads_top_sources_and_clock():
    info = tool.parse_info_yaml(INFO_YAML)
    assert info["top_module"] == "tt_um_lahnb_sgdma"
    assert info["clock_hz"] == 66_000_000
    assert info["source_files"] == [
        "top.v",
        "types.svh",
        "qspi_engine.sv",
        "sys_controller.sv",
    ]


def test_selftest_parse_info_yaml_rejects_missing_top():
    with pytest.raises(tool.FpgaToolError, match="top_module"):
        tool.parse_info_yaml("clock_hz: 1\nsource_files:\n  - a.v\n")


def test_selftest_wrap_fpga_top_replaces_placeholder():
    wrapped = tool.wrap_fpga_top(
        "tt_um_placeholder u_dut (.__tt_um_placeholder);",
        "tt_um_lahnb_sgdma",
    )
    assert "tt_um_lahnb_sgdma" in wrapped
    assert tool.DESIGN_PLACEHOLDER not in wrapped


def test_selftest_wrap_fpga_top_requires_placeholder():
    with pytest.raises(tool.FpgaToolError, match="placeholder"):
        tool.wrap_fpga_top("module tt_fpga_top;", "tt_um_lahnb_sgdma")


def test_selftest_yosys_harden_script_splits_wrapper_and_slang_rtl():
    script = tool.yosys_harden_script(
        "wrap.v",
        ["types.svh", "top.v"],
        "out.json",
    )
    assert script.startswith("read_verilog -sv wrap.v;")
    assert "read_slang -D SYNTH types.svh top.v;" in script
    assert "synth_ice40 -top tt_fpga_top -json out.json" in script
    assert script.find("read_verilog") < script.find("read_slang")


def test_selftest_bitstream_filename_matches_shuttle_name():
    assert tool.bitstream_filename("tt_um_lahnb_sgdma") == "tt_um_lahnb_sgdma.bin"


def test_selftest_fpga_clock_hz_uses_the_nextpnr_constraint(monkeypatch):
    monkeypatch.delenv("TT_FPGA_FREQ", raising=False)
    assert tool.fpga_clock_hz() == 12_000_000
    monkeypatch.setenv("TT_FPGA_FREQ", "25.5")
    assert tool.fpga_clock_hz() == 25_500_000


def test_selftest_find_tt_dir_prefers_env_then_extra(tmp_path, monkeypatch):
    env_dir = tmp_path / "env-tt"
    env_dir.mkdir()
    (env_dir / "tt_fpga.py").write_text("# official\n", encoding="utf-8")
    extra_dir = tmp_path / "extra-tt"
    extra_dir.mkdir()
    (extra_dir / "tt_fpga.py").write_text("# extra\n", encoding="utf-8")
    monkeypatch.setenv("TT_SUPPORT_TOOLS", str(env_dir))
    found = tool.find_tt_dir(tmp_path, extra=[extra_dir])
    assert found == env_dir.resolve()


def test_selftest_find_tt_dir_uses_extra_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("TT_SUPPORT_TOOLS", raising=False)
    monkeypatch.delenv("TT_FPGA_DIR", raising=False)
    extra_dir = tmp_path / "extra-tt"
    extra_dir.mkdir()
    (extra_dir / "tt_fpga.py").write_text("# extra\n", encoding="utf-8")
    found = tool.find_tt_dir(tmp_path, extra=[extra_dir])
    assert found == extra_dir.resolve()


def test_selftest_find_tt_dir_errors_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("TT_SUPPORT_TOOLS", raising=False)
    monkeypatch.delenv("TT_FPGA_DIR", raising=False)
    with pytest.raises(tool.FpgaToolError, match="tt_fpga.py not found"):
        tool.find_tt_dir(tmp_path, extra=[tmp_path / "missing"])


def test_selftest_pcf_path_maps_breakout_names(tmp_path):
    fpga = tmp_path / "fpga"
    fpga.mkdir()
    classic = fpga / "tt_fpga_top.pcf"
    fox = fpga / "tt_fpga_fabricfoxv2.pcf"
    classic.write_text("classic\n", encoding="utf-8")
    fox.write_text("fox\n", encoding="utf-8")
    assert tool.pcf_path(tmp_path, "classic") == classic
    assert tool.pcf_path(tmp_path, "fabricfox") == fox
    with pytest.raises(tool.FpgaToolError, match="unknown breakout"):
        tool.pcf_path(tmp_path, "icebreaker")


def test_selftest_stage_project_copies_listed_sources_only(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "info.yaml").write_text(INFO_YAML, encoding="utf-8")
    for name in ("top.v", "types.svh", "qspi_engine.sv", "sys_controller.sv"):
        (tmp_path / "src" / name).write_text("// %s\n" % name, encoding="utf-8")
    (tmp_path / "src" / "scratch.v").write_text("// leave me\n", encoding="utf-8")
    dest = tmp_path / "stage"
    staged = tool.stage_project(tmp_path, dest)
    assert (staged / "info.yaml").is_file()
    assert (staged / "src" / "top.v").read_text(encoding="utf-8") == "// top.v\n"
    assert not (staged / "src" / "scratch.v").exists()


def test_selftest_stage_project_rejects_missing_rtl(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "info.yaml").write_text(INFO_YAML, encoding="utf-8")
    (tmp_path / "src" / "top.v").write_text("module x;\n", encoding="utf-8")
    with pytest.raises(tool.FpgaToolError, match="RTL sources missing"):
        tool.stage_project(tmp_path, tmp_path / "stage")


def test_selftest_upload_bitstream_invokes_mpremote(tmp_path, monkeypatch):
    bin_path = tmp_path / "tt_um_lahnb_sgdma.bin"
    bin_path.write_bytes(b"\x00\x01")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return type("Done", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    remote = tool.upload_bitstream(bin_path, port="/dev/ttyACM1", mpremote="mpremote")
    assert remote == "/bitstreams/tt_um_lahnb_sgdma.bin"
    assert calls[0][:5] == ["mpremote", "connect", "/dev/ttyACM1", "fs", "mkdir"]
    assert calls[1][:5] == ["mpremote", "connect", "/dev/ttyACM1", "fs", "cp"]
    assert calls[1][-1] == ":/bitstreams/tt_um_lahnb_sgdma.bin"
    assert calls[2] == ["mpremote", "connect", "/dev/ttyACM1", "reset"]
    assert calls[3] == [
        "mpremote",
        "connect",
        "/dev/ttyACM1",
        "resume",
        "exec",
        "True",
    ]


def test_selftest_upload_bitstream_missing_file(tmp_path):
    with pytest.raises(tool.FpgaToolError, match="bitstream not found"):
        tool.upload_bitstream(tmp_path / "missing.bin")


def _staging_repo(tmp_path):
    """A minimal repo tree: info.yaml plus the RTL it lists."""
    (tmp_path / "src").mkdir()
    (tmp_path / "info.yaml").write_text(INFO_YAML, encoding="utf-8")
    for name in ("top.v", "types.svh", "qspi_engine.sv", "sys_controller.sv"):
        (tmp_path / "src" / name).write_text("// %s\n" % name, encoding="utf-8")
    return tmp_path


def test_selftest_bitstream_inputs_are_info_yaml_plus_listed_rtl(tmp_path):
    root = _staging_repo(tmp_path)
    inputs = tool.bitstream_inputs(root)
    assert inputs[0] == root / "info.yaml"
    assert set(inputs[1:]) == {
        root / "src" / name
        for name in ("top.v", "types.svh", "qspi_engine.sv", "sys_controller.sv")
    }


def test_selftest_stale_inputs_empty_when_the_bitstream_is_newest(tmp_path):
    root = _staging_repo(tmp_path)
    bin_path = tmp_path / "out.bin"
    bin_path.write_bytes(b"\x00")
    assert tool.stale_inputs(bin_path, root=root) == ()
    assert tool.describe_staleness(bin_path, root=root) == "bitstream is current"


def test_selftest_stale_inputs_lists_rtl_touched_after_the_build(tmp_path):
    root = _staging_repo(tmp_path)
    bin_path = tmp_path / "out.bin"
    bin_path.write_bytes(b"\x00")
    built = bin_path.stat().st_mtime
    edited = root / "src" / "qspi_engine.sv"
    os.utime(edited, (built + 10, built + 10))
    assert tool.stale_inputs(bin_path, root=root) == (edited,)
    text = tool.describe_staleness(bin_path, root=root)
    assert "1 input(s) newer" in text
    assert "qspi_engine.sv" in text


def test_selftest_stale_inputs_requires_the_bitstream_to_exist(tmp_path):
    root = _staging_repo(tmp_path)
    with pytest.raises(tool.FpgaToolError, match="bitstream not found"):
        tool.stale_inputs(tmp_path / "missing.bin", root=root)


def test_selftest_cli_harden_help_lists_breakouts():
    parser = build_parser()
    help_text = parser.format_help()
    assert "bitstream" in help_text
    assert "upload" in help_text
    assert "configure" in help_text


def test_selftest_cli_upload_defaults_to_refusing_a_stale_bitstream():
    parser = build_parser()
    assert parser.parse_args(["upload"]).stale is False
    assert parser.parse_args(["upload", "--stale"]).stale is True


def test_selftest_cli_configure_requires_an_action():
    with pytest.raises(SystemExit) as exc:
        main(["configure"])
    assert exc.value.code == 2


def test_selftest_repo_info_yaml_is_readable():
    info = tool.load_project_info()
    assert info["top_module"] == "tt_um_lahnb_sgdma"
    assert "top.v" in info["source_files"]
    assert info["clock_hz"] == 66_000_000
