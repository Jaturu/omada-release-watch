import json
from pathlib import Path

import pytest

from scripts.render_release_report import PlatformSummary, summarize_platform
from scripts.rescan_status import main, render_status, verdict

# --- The verdict: the one line someone reads before deciding to run it --------


def _summary(slug="linux-amd64", **overrides):
    defaults = {
        "slug": slug,
        "digest": "sha256:aaa",
        "sbom": {"total": 3, "by_type": {"library": 3}},
        "grype": {
            "by_severity": {},
            "suppressed": [],
            "findings": [],
            "scanned": "2026-08-05 10:28 UTC",
            "db_built": "2026-08-05 07:04 UTC",
        },
        "trivy": {"by_severity": {}, "findings": [], "scanned": ""},
    }
    defaults.update(overrides)
    return PlatformSummary(**defaults)


def _grype(by_severity, findings=None, suppressed=None):
    return {
        "by_severity": by_severity,
        "findings": findings or [],
        "suppressed": suppressed or [],
        "scanned": "2026-08-05 10:28 UTC",
        "db_built": "2026-08-05 07:04 UTC",
    }


def test_verdict_is_clean_when_nothing_is_found():
    assert verdict([_summary()]) == "No findings."


def test_verdict_separates_low_severity_from_actionable():
    """Medium findings with no fix are the steady state here. Calling that
    'action needed' would train the reader to ignore the line."""
    out = verdict([_summary(grype=_grype({"Medium": 8, "Low": 1}))])
    assert "No High or Critical" in out
    assert "9" in out


def test_verdict_calls_out_high_severity():
    out = verdict([_summary(grype=_grype({"High": 2, "Medium": 1}))])
    assert out.startswith("Action needed")
    assert "2" in out


def test_verdict_counts_critical_as_actionable():
    assert verdict([_summary(grype=_grype({"Critical": 1}))]).startswith("Action needed")


def test_verdict_totals_across_platforms():
    out = verdict(
        [
            _summary("linux-amd64", grype=_grype({"High": 1})),
            _summary("linux-arm64", grype=_grype({"High": 1})),
        ]
    )
    assert "2" in out


def test_verdict_includes_trivy_findings():
    """Either scanner finding something high is enough to act on."""
    summary = _summary()
    summary.trivy = {"by_severity": {"High": 1}, "findings": [], "scanned": ""}
    assert verdict([summary]).startswith("Action needed")


def test_verdict_ignores_suppressed_findings():
    """A suppression is a decision already taken. Counting it as actionable
    would make the page permanently red."""
    suppressed = [
        {
            "id": "CVE-1",
            "severity": "High",
            "package": "python",
            "version": "3.13.14",
            "rules": ["CVE-1"],
            "reason": "unreachable. Tracked in #5",
            "fix": "3.15.0",
        }
    ]
    assert verdict([_summary(grype=_grype({}, suppressed=suppressed))]) == "No findings."


# --- Rendering ----------------------------------------------------------------


def test_render_status_names_the_release_and_when_it_was_checked():
    out = render_status(
        image="docker.io/jaturu/omada-release-watch",
        tag="v1.0.0-4",
        index_digest="sha256:index",
        summaries=[_summary()],
    )
    assert "v1.0.0-4" in out
    assert "docker.io/jaturu/omada-release-watch" in out
    assert "2026-08-05 10:28 UTC" in out
    assert "sha256:index" in out


def test_render_status_leads_with_the_verdict():
    out = render_status(
        image="img", tag="v1", index_digest="sha256:i",
        summaries=[_summary(grype=_grype({"High": 1}))],
    )
    head = "\n".join(out.splitlines()[:8])
    assert "Action needed" in head


def test_render_status_says_it_is_generated_and_not_signed():
    out = render_status(
        image="img", tag="v1", index_digest="sha256:i", summaries=[_summary()]
    )
    assert "not signed" in out


def test_render_status_shows_a_fix_becoming_available_for_a_suppression():
    """The reason a suppression is allowed is that no fix exists on our line.
    When one appears the page has to show it, which is what #5 asks for."""
    suppressed = [
        {
            "id": "CVE-2026-15308",
            "severity": "High",
            "package": "python",
            "version": "3.13.14",
            "rules": ["CVE-2026-15308"],
            "reason": "no 3.13 backport available. Tracked in #5",
            "fix": "3.13.15",
        }
    ]
    out = render_status(
        image="img", tag="v1", index_digest="sha256:i",
        summaries=[_summary(grype=_grype({}, suppressed=suppressed))],
    )
    assert "3.13.15" in out
    assert "CVE-2026-15308" in out


def test_render_status_escapes_scanner_text():
    out = render_status(
        image="img", tag="v1", index_digest="sha256:i",
        summaries=[
            _summary(
                grype=_grype(
                    {"High": 1},
                    findings=[
                        {
                            "id": "CVE-1",
                            "severity": "High",
                            "packages": ["evil | <img src=x>"],
                            "fix": "",
                        }
                    ],
                )
            )
        ],
    )
    assert "<img" not in out


def test_render_status_is_stable_for_identical_input():
    """The workflow commits only when the file changes, so equal scans must
    produce byte-identical output or it would commit every day."""
    args = dict(
        image="img", tag="v1", index_digest="sha256:i", summaries=[_summary()]
    )
    assert render_status(**args) == render_status(**args)


# --- End to end against a real re-scan ----------------------------------------


@pytest.fixture
def report_dir():
    return Path(__file__).parent / "fixtures" / "reports"


def test_main_renders_from_real_reports(report_dir, tmp_path):
    out = tmp_path / "status.md"
    rc = main(
        [
            "--image", "docker.io/jaturu/omada-release-watch",
            "--tag", "v1.0.0-4",
            "--index-digest", "sha256:index",
            "--reports-dir", str(report_dir),
            "--platform", "linux-amd64=sha256:amd",
            "--expect-platforms", "1",
            "--output", str(out),
        ]
    )
    assert rc == 0
    body = out.read_text()
    assert "No High or Critical" in body
    assert "CVE-2026-11940" in body


def test_main_refuses_a_missing_platform(report_dir, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--image", "img",
                "--tag", "v1",
                "--index-digest", "sha256:i",
                "--reports-dir", str(report_dir),
                "--platform", "linux-amd64=sha256:amd",
                "--expect-platforms", "2",
                "--output", str(tmp_path / "s.md"),
            ]
        )
    assert excinfo.value.code != 0


def test_summarize_platform_still_reads_the_fixture(report_dir):
    summary = summarize_platform(
        slug="linux-amd64",
        digest="sha256:amd",
        sbom=json.loads((report_dir / "sbom.cyclonedx.linux-amd64.json").read_text()),
        grype=json.loads((report_dir / "grype-report.linux-amd64.json").read_text()),
        trivy=json.loads((report_dir / "trivy-report.linux-amd64.json").read_text()),
    )
    # Every suppression carries a fix version today, all on the 3.15 line.
    assert all(s["fix"] for s in summary.grype["suppressed"])
