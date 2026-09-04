from pathlib import Path

from marimo_export import ExportRepository, ExportSpec, build, open_export
from marimo_export.descriptors import MarimoOutputDescriptor

ROOT = Path(__file__).parents[3]
EXAMPLE = ROOT / "examples" / "quickstart"
QUICKSTART = ROOT / "docs" / "guide" / "getting-started.md"
PYPI_README = ROOT / "packages" / "python" / "README.md"


def _documented_source(document_path: Path, name: str, language: str) -> str:
    document = document_path.read_text(encoding="utf-8")
    opening = f"<!-- quickstart-source: {name} -->\n\n```{language}\n"
    return document.split(opening, 1)[1].split("\n```", 1)[0] + "\n"


def test_quickstart_sources_match_the_documented_files() -> None:
    for document in (QUICKSTART, PYPI_README):
        assert _documented_source(document, "report.py", "python") == (
            EXAMPLE / "report.py"
        ).read_text(encoding="utf-8")
        assert _documented_source(document, "report.export.yaml", "yaml") == (
            EXAMPLE / "report.export.yaml"
        ).read_text(encoding="utf-8")


def test_quickstart_builds_and_reads_two_states_and_two_outputs(tmp_path: Path) -> None:
    output = tmp_path / "export"

    with ExportRepository.open(tmp_path / "repository") as repository:
        result = build(
            EXAMPLE / "report.py",
            spec=ExportSpec.from_file(EXAMPLE / "report.export.yaml"),
            output=output,
            repository=repository,
        )

    assert result.verification.states == 2
    assert result.verification.outputs == 4
    assert result.verification.assets == 2
    assert result.verification.bytes_verified > 0
    notebook_export = open_export(output)
    weekly = notebook_export.state("weekly")
    monthly = notebook_export.state("monthly")
    assert dict(weekly.inputs) == {"days": 7}
    assert dict(monthly.inputs) == {"days": 30}
    assert weekly.output("summary").json() == {
        "days": 7,
        "label": "Last 7 days",
    }
    assert monthly.output("summary").json() == {
        "days": 30,
        "label": "Last 30 days",
    }
    reports = (weekly.output("report"), monthly.output("report"))
    descriptors = tuple(report.descriptor for report in reports)
    assert all(isinstance(descriptor, MarimoOutputDescriptor) for descriptor in descriptors)
    assert all(report.asset_bytes() for report in reports)
    assert (
        len(
            {
                descriptor.asset.sha256
                for descriptor in descriptors
                if isinstance(descriptor, MarimoOutputDescriptor)
            }
        )
        == 2
    )
