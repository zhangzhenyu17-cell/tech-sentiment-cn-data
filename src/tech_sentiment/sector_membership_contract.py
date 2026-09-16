from __future__ import annotations

import pandas as pd

from .sector_data_gate import MembershipEvidenceAudit, audit_membership_evidence


# 931152 design starts on the official launch date, so point-in-time history needs
# an initial launch anchor as well as every scheduled semiannual rebalance through
# the end of the 2019-2023 design interval.  A current constituent snapshot cannot
# substitute for any of these periods.
SECTOR_931152_DESIGN_MEMBERSHIP_PERIODS = (
    "2019-04",  # official launch anchor, 2019-04-22
    "2019-06",
    "2019-12",
    "2020-06",
    "2020-12",
    "2021-06",
    "2021-12",
    "2022-06",
    "2022-12",
    "2023-06",
    "2023-12",
)


def audit_931152_design_membership(manifest: pd.DataFrame) -> MembershipEvidenceAudit:
    """Fail closed unless the full 931152 design-period membership chain exists."""

    return audit_membership_evidence(
        manifest,
        expected_periods=SECTOR_931152_DESIGN_MEMBERSHIP_PERIODS,
    )
