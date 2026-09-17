from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "research" / "build_931152_oos_inputs.py"
SPEC = importlib.util.spec_from_file_location("build_931152_oos_inputs", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _LoginResult:
    def __init__(self, error_code: str, error_msg: str = "") -> None:
        self.error_code = error_code
        self.error_msg = error_msg


class _RowsResult:
    def __init__(self, rows: list[list[str]], *, error_code: str = "0", error_msg: str = "") -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = ["value"]
        self._rows = list(rows)
        self._current: list[str] | None = None

    def next(self) -> bool:
        if not self._rows:
            return False
        self._current = self._rows.pop(0)
        return True

    def get_row_data(self) -> list[str]:
        assert self._current is not None
        return self._current


class _FakeBaoStock:
    def __init__(self, login_codes: list[str]) -> None:
        self.login_codes = list(login_codes)
        self.login_calls = 0
        self.logout_calls = 0

    def login(self) -> _LoginResult:
        self.login_calls += 1
        code = self.login_codes.pop(0) if self.login_codes else "0"
        return _LoginResult(code, "temporary login failure" if code != "0" else "")

    def logout(self) -> None:
        self.logout_calls += 1


def test_baostock_login_retries_then_succeeds() -> None:
    client = _FakeBaoStock(["1", "0"])
    sleeps: list[float] = []
    MODULE._baostock_login_with_retry(
        client,
        retries=2,
        backoff_seconds=0.5,
        sleep_fn=sleeps.append,
    )
    assert client.login_calls == 2
    assert client.logout_calls == 1
    assert sleeps == [0.5]


def test_baostock_query_reconnects_after_transient_failure() -> None:
    client = _FakeBaoStock(["0"])
    responses = [RuntimeError("temporary socket failure"), _RowsResult([["ok"]])]
    sleeps: list[float] = []

    def query():
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    rows, fields = MODULE._baostock_query_rows_with_retry(
        client,
        query,
        stage="history:600276",
        retries=1,
        backoff_seconds=0.25,
        sleep_fn=sleeps.append,
    )
    assert rows == [["ok"]]
    assert fields == ["value"]
    assert client.logout_calls == 1
    assert client.login_calls == 1
    assert sleeps == [0.25]


def test_baostock_query_never_accepts_empty_semantic_response() -> None:
    client = _FakeBaoStock(["0"])
    responses = [_RowsResult([]), _RowsResult([])]

    def query():
        return responses.pop(0)

    try:
        MODULE._baostock_query_rows_with_retry(
            client,
            query,
            stage="stock_basic:600276",
            retries=1,
            backoff_seconds=0.0,
            sleep_fn=lambda _: None,
        )
    except RuntimeError as exc:
        assert "exhausted retries" in str(exc)
    else:
        raise AssertionError("empty BaoStock responses must remain fail-closed")
