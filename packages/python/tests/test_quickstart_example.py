from pathlib import Path

from marimo_export import ExportRepository, ExportSpec, build, open_export

ROOT = Path(__file__).parents[3]
EXAMPLE = ROOT / "examples" / "quickstart"
QUICKSTART = ROOT / "docs" / "guide" / "getting-started.md"


def _documented_source(name: str, language: str) -> str:
    document = QUICKSTART.read_text(encoding="utf-8")
    opening = f"<!-- quickstart-source: {name} -->\n\n```{language}\n"
    return document.split(opening, 1)[1].split("\n```", 1)[0] + "\n"


def test_quickstart_sources_match_the_documented_files() -> None:
    assert _documented_source("report.py", "python") == (EXAMPLE / "report.py").read_text(
        encoding="utf-8"
    )
    assert _documented_source("report.export.yaml", "yaml") == (
        EXAMPLE / "report.export.yaml"
    ).read_text(encoding="utf-8")


def test_quickstart_builds_and_reads_two_states(tmp_path: Path) -> None:
    output = tmp_path / "export"

    with ExportRepository.open(tmp_path / "repository") as repository:
        result = build(
            EXAMPLE / "report.py",
            spec=ExportSpec.from_file(EXAMPLE / "report.export.yaml"),
            output=output,
            repository=repository,
        )

    assert result.verification.states == 2
    assert result.verification.outputs == 2
    notebook_export = open_export(output)
    assert notebook_export.state("weekly").output("summary").json() == {
        "days": 7,
        "label": "Last 7 days",
    }
    assert notebook_export.state("monthly").output("summary").json() == {
        "days": 30,
        "label": "Last 30 days",
    }
