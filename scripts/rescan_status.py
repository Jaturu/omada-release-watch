#!/usr/bin/env python3
"""Render the living security status of the current release.

The release report published with a version is a snapshot and never changes.
This page is rewritten on a schedule by re-scanning the SBOM already attached
to the published image, so it answers whether the current release is safe to
run today rather than what was known when it was built.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.render_release_report import (
    PlatformSummary,
    cell,
    summarize_platform,
)

# A finding at or above this level is worth a reader's attention. Everything
# below is the steady state and would make the verdict meaningless.
ACTIONABLE = ("Critical", "High")


def _total(summaries: list[PlatformSummary], severities: tuple[str, ...]) -> int:
    return sum(
        counts.get(name, 0)
        for summary in summaries
        for counts in (summary.grype["by_severity"], summary.trivy["by_severity"])
        for name in severities
    )


def verdict(summaries: list[PlatformSummary]) -> str:
    """One line stating whether anything needs doing.

    Suppressed findings are excluded because they are decisions already
    recorded, not open work.
    """
    actionable = _total(summaries, ACTIONABLE)
    if actionable:
        return (
            f"Action needed: {actionable} High or Critical finding(s) "
            "against the current release."
        )
    everything = sum(
        sum(counts.values())
        for summary in summaries
        for counts in (summary.grype["by_severity"], summary.trivy["by_severity"])
    )
    if everything:
        return (
            f"No High or Critical findings. {everything} lower-severity "
            "finding(s) recorded below."
        )
    return "No findings."


def render_status(
    image: str,
    tag: str,
    index_digest: str,
    summaries: list[PlatformSummary],
) -> str:
    checked = next(
        (
            s.grype.get("scanned") or s.trivy.get("scanned")
            for s in summaries
            if s.grype.get("scanned") or s.trivy.get("scanned")
        ),
        "",
    )
    database = next((s.grype.get("db_built") for s in summaries if s.grype.get("db_built")), "")

    out: list[str] = []
    out.append("# Security status")
    out.append("")
    out.append(verdict(summaries))
    out.append("")
    out.append(f"Release `{tag}`, published as `{image}:{tag}` and `{image}:latest`.")
    out.append(f"Manifest list `{index_digest}`.")
    if checked:
        line = f"Re-scanned {checked}"
        if database:
            line += f" against a vulnerability database built {database}"
        out.append(f"{line}.")
    out.append("")
    out.append(
        "This page is regenerated on a schedule from the CycloneDX SBOM "
        "attached to the published image, so it reflects what the scanners "
        "know now rather than what they knew at build time. It is generated "
        "output and is not signed. The per-release pages under Releases are "
        "the build-time snapshots and do not change."
    )

    for summary in summaries:
        out.append("")
        out.append(f"## {summary.slug}")
        out.append("")
        out.append(f"Image digest: `{summary.digest}`")
        out.append("")

        findings = summary.grype["findings"] + summary.trivy["findings"]
        if not findings:
            out.append("No findings.")
        else:
            out.append("| CVE | Severity | Packages | Fixed in |")
            out.append("|---|---|---|---|")
            for item in findings:
                packages = ", ".join(cell(p) for p in item["packages"])
                fix = cell(item["fix"]) if item["fix"] else "No fix available"
                out.append(
                    f"| {cell(item['id'])} | {cell(item['severity'])} "
                    f"| {packages} | {fix} |"
                )

        if summary.grype["suppressed"]:
            out.append("")
            out.append(
                "Suppressed by `.grype-ignore.yaml`. A fix version appearing "
                "on a line this image can actually move to retires the "
                "suppression, which is what issue #5 tracks."
            )
            out.append("")
            out.append("| CVE | Severity | Package | Fixed in | Reason |")
            out.append("|---|---|---|---|---|")
            for item in summary.grype["suppressed"]:
                package = item["package"]
                if item["version"]:
                    package = f"{package} {item['version']}"
                fix = cell(item["fix"]) if item["fix"] else "No fix available"
                reason = cell(item["reason"]) if item["reason"] else "No reason recorded"
                out.append(
                    f"| {cell(item['id'])} | {cell(item['severity'])} "
                    f"| {cell(package)} | {fix} | {reason} |"
                )

    out.append("")
    return "\n".join(out)


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--index-digest", required=True)
    parser.add_argument("--reports-dir", required=True, type=Path)
    parser.add_argument(
        "--platform", action="append", required=True, metavar="SLUG=DIGEST"
    )
    parser.add_argument("--expect-platforms", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    summaries = []
    for entry in args.platform:
        slug, _, digest = entry.partition("=")
        if not slug or not digest:
            parser.error(f"expected SLUG=DIGEST, got {entry!r}")
        summaries.append(
            summarize_platform(
                slug=slug,
                digest=digest,
                sbom=_load(args.reports_dir / f"sbom.cyclonedx.{slug}.json"),
                grype=_load(args.reports_dir / f"grype-report.{slug}.json"),
                trivy=_load(args.reports_dir / f"trivy-report.{slug}.json"),
            )
        )

    if args.expect_platforms is not None and len(summaries) != args.expect_platforms:
        parser.error(
            f"expected {args.expect_platforms} platforms, rendered "
            f"{len(summaries)}: {[s.slug for s in summaries]}"
        )

    body = render_status(args.image, args.tag, args.index_digest, summaries)
    if args.output:
        args.output.write_text(body)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
