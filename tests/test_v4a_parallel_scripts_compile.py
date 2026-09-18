from pathlib import Path


PARALLEL_SCRIPTS = (
    "scripts/build_v4a_shared_calendar.py",
    "scripts/write_v4a_stage_receipt.py",
    "scripts/verify_v4a_stage_receipt.py",
    "scripts/aggregate_v4a_issuer_shards.py",
    "scripts/materialize_v4a_fundamental_earnings_shard.py",
    "scripts/materialize_v4a_price_shard.py",
    "scripts/materialize_v4a_policy_stage.py",
    "scripts/assemble_v4a_derived_pit.py",
)


def test_v4a_parallel_cli_sources_compile() -> None:
    for value in PARALLEL_SCRIPTS:
        path = Path(value)
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
