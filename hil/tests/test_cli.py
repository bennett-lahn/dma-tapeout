"""Dispatch for ``python -m hil``: bitstream subset vs the test runner."""

from hil.cli import FPGA_COMMANDS, dispatch


def test_selftest_fpga_commands_are_bitstream_subset():
    assert FPGA_COMMANDS == frozenset(
        {"bitstream", "harden", "upload", "configure", "doctor"}
    )


def test_selftest_dispatch_bitstream_verbs_do_not_enter_test_runner(monkeypatch):
    seen = []

    def fake_fpga(argv):
        seen.append(list(argv))
        return 0

    def fail_runner(argv):
        raise AssertionError("test runner should not run for %r" % (argv,))

    monkeypatch.setattr("hil.fpga.__main__.main", fake_fpga)
    monkeypatch.setattr("hil.run.main", fail_runner)
    assert dispatch(["bitstream", "--help"]) == 0
    assert dispatch(["upload", "--port", "/dev/ttyACM1"]) == 0
    assert dispatch(["doctor"]) == 0
    assert [row[0] for row in seen] == ["bitstream", "upload", "doctor"]


def test_selftest_dispatch_other_args_go_to_test_runner(monkeypatch):
    seen = []

    def fail_fpga(argv):
        raise AssertionError("fpga tools should not run for %r" % (argv,))

    def fake_runner(argv):
        seen.append(list(argv))
        return 0

    monkeypatch.setattr("hil.fpga.__main__.main", fail_fpga)
    monkeypatch.setattr("hil.run.main", fake_runner)
    assert dispatch(["--target=fpga", "--bitstream=reuse"]) == 0
    assert dispatch([]) == 0
    assert seen[0] == ["--target=fpga", "--bitstream=reuse"]
    assert seen[1] == []
