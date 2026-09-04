"""Pure-Python dispose contract tests (no simulator).

tb-hs-02 unexpected blocked, tb-life-05 >=1 vs exact count, truncated_counts,
tb-life-06 REVIEW allowlist.
"""

from common.constants import RESULT_BLOCKED, RESULT_FAIL, RESULT_NA, RESULT_PASS
from common.dispose import (
    DisposeReport,
    Expected,
    FORBID,
    Finding,
    REQUIRE,
    REVIEW,
    expect,
)

def test_expect_default_is_at_least_one():
    """tb-life-05: bare expect(ID) means >=1; count=N is exact."""
    assert expect("Q-MUX").matches(1)
    assert expect("Q-MUX").matches(3)
    assert not expect("Q-MUX").matches(0)
    assert expect("Q-MUX", count=2).matches(2)
    assert not expect("Q-MUX", count=2).matches(1)
    assert ">=1" in str(expect("Q-MUX"))

def test_report_blocked_excludes_na():
    """tb-hs-02: na is never treated as blocked."""
    report = DisposeReport(
        test="t",
        results={"CHK-HS-OPCODE": RESULT_PASS, "CHK-CTRL-REQ-GATE": RESULT_NA},
    )
    assert report.blocked() == []
    report.results["CHK-HS-REQ-STABLE"] = RESULT_BLOCKED
    assert report.blocked() == ["CHK-HS-REQ-STABLE"]

def test_truncated_counts_separate_from_ordinary():
    """tb-life-05: truncated hits are counted under truncated_counts."""
    report = DisposeReport(
        test="t",
        counts={"CHK-HS-RDATA-COUNT": 0},
        truncated_counts={"CHK-HS-RDATA-COUNT": 2},
        reset_truncated=[
            Finding("CHK-HS-RDATA-COUNT", "hs", 1.0, "partial", True),
            Finding("CHK-HS-RDATA-COUNT", "hs", 2.0, "partial", True),
        ],
    )
    assert report.counts.get("CHK-HS-RDATA-COUNT", 0) == 0
    assert report.truncated_counts["CHK-HS-RDATA-COUNT"] == 2
    assert report.failures() == []
