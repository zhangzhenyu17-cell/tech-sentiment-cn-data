from __future__ import annotations

import pandas as pd

from tech_sentiment.csi_adjustment_evidence import extract_index_changes_from_sheets
from tech_sentiment.sina_index_membership_evidence import parse_membership_intervals


def _sheets(index_code: str, add_symbol: str, remove_symbol: str) -> dict[str, pd.DataFrame]:
    return {
        "调入": pd.DataFrame(
            [
                ["说明", None, None],
                ["指数代码", "证券代码", "证券简称"],
                [index_code, add_symbol, "调入示例"],
                ["931152", "600276", "其他指数"],
            ]
        ),
        "调出": pd.DataFrame(
            [
                ["指数代码", "证券代码", "证券简称"],
                [index_code, remove_symbol, "调出示例"],
                ["000300", "600519", "其他指数"],
            ]
        ),
    }


def test_csi_adjustment_workbook_filter_is_generic_for_followon_indices() -> None:
    defense = extract_index_changes_from_sheets(
        _sheets("399973", "600893", "600879"), index_code="399973"
    )
    securities = extract_index_changes_from_sheets(
        _sheets("399975", "600030", "000166"), index_code="399975"
    )

    assert defense == {"add": ["600893"], "remove": ["600879"]}
    assert securities == {"add": ["600030"], "remove": ["000166"]}


def test_sina_exact_code_parser_is_generic_for_followon_indices() -> None:
    html = """
    <table>
      <tr><td>中证国防</td><td>399973</td><td>2014-04-15</td><td>2020-06-15</td></tr>
      <tr><td>衍生代码</td><td>399973CNY01</td><td>2015-01-01</td><td></td></tr>
      <tr><td>证券公司</td><td>399975</td><td>2013-07-15</td><td></td></tr>
    </table>
    """

    defense = parse_membership_intervals(
        html,
        symbol="600893",
        source_url="https://example.invalid/600893",
        response_sha256="defense",
        index_code="399973",
    )
    securities = parse_membership_intervals(
        html,
        symbol="600030",
        source_url="https://example.invalid/600030",
        response_sha256="securities",
        index_code="399975",
    )

    assert [(row.index_code, row.start_date, row.end_date) for row in defense] == [
        ("399973", "2014-04-15", "2020-06-15")
    ]
    assert [(row.index_code, row.start_date, row.end_date) for row in securities] == [
        ("399975", "2013-07-15", "")
    ]
