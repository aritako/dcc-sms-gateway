from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "load_test.py"
SPEC = importlib.util.spec_from_file_location("load_test_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
load_test = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = load_test
SPEC.loader.exec_module(load_test)


def test_percentile_uses_nearest_rank() -> None:
  assert load_test.percentile([], 0.95) is None
  assert load_test.percentile([1.0], 0.95) == 1.0
  assert load_test.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.50) == 3.0
  assert load_test.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0


def test_max_in_window_is_rolling() -> None:
  assert load_test.max_in_window([], 1.0) == 0
  assert load_test.max_in_window([1.0, 1.2, 2.0, 2.1, 2.2], 1.0) == 3


def test_carrier_delta_and_run_scoped_submits() -> None:
  first = uuid.UUID("01a1f24c-0e7f-7050-b73d-9c60d481a10e")
  second = uuid.UUID("01a1f24c-0e77-7202-a588-a997aec3f90b")
  before = load_test.CarrierStats(10, 1, 2, 3, 4, 5, {})
  after = load_test.CarrierStats(
    15,
    3,
    2,
    5,
    6,
    9,
    {str(first): 3, str(second): 2, "other-message": 7},
  )

  assert load_test.carrier_delta(before, after) == {
    "submits": 5,
    "s400": 2,
    "s429": 0,
    "s503": 2,
    "timeouts": 2,
  }
  assert load_test.run_submits(after, [first, second]) == 5


def test_load_config_allows_short_smoke_and_rejects_bad_batch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("COUNT", "50")
  monkeypatch.setenv("DRAIN_TIMEOUT", "30")
  monkeypatch.delenv("BATCH_SIZE", raising=False)

  config = load_test.load_config(["load_test.py"])
  assert config.count == 50
  assert config.drain_timeout == 30

  monkeypatch.setenv("BATCH_SIZE", "1001")
  with pytest.raises(load_test.LoadError, match="BATCH_SIZE"):
    load_test.load_config(["load_test.py"])


def test_postgres_dsn_uses_sync_driver() -> None:
  assert (
    load_test.postgres_dsn("postgresql+asyncpg://sms:sms@localhost:5432/sms")
    == "postgresql://sms:sms@localhost:5432/sms"
  )


def _tenant(**overrides: int | str) -> object:
  values = {
    "name": "Acme",
    "opening": 1_000,
    "ledger": -300,
    "balance": 700,
    "ledger_before": -100,
    "balance_before": 900,
    "run_ledger": -200,
    "run_messages": 10,
    "run_charges": 10,
    "run_refunds": 0,
  }
  values.update(overrides)
  return load_test.TenantBilling(**values)


def _audit(tenant: object) -> object:
  return load_test.BillingAudit(
    tenants=[tenant],
    wrong_charge_counts=0,
    wrong_charge_amounts=0,
    wrong_refund_amounts=0,
    ineligible_refunds=0,
    unrefunded_failures=0,
    duplicate_refunds=0,
    duplicate_client_refs=0,
    missing_client_refs=0,
    missing_messages=0,
  )


def test_billing_audit_checks_lifetime_and_run_ledgers() -> None:
  assert _audit(_tenant()).ok

  wrong_run = _tenant(run_ledger=-201)
  assert not _audit(wrong_run).ok

  wrong_lifetime = _tenant(balance=701)
  assert not _audit(wrong_lifetime).ok
