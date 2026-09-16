from pathlib import Path
import csv


def test_secondary_source_registry_has_no_production_authority() -> None:
    path = Path(__file__).parents[1] / "docs/secondary_evidence_source_registry.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows
    assert {row["production_authority"] for row in rows} == {"none"}
    assert any(row["provider"] == "EastMoney/Tiantian Fund" for row in rows)
    assert any(row["provider"] == "Sina Finance" for row in rows)
