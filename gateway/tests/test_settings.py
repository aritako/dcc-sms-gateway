import pytest

from app.settings import load_settings


def test_missing_required_settings_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "REDIS_URL", "DLR_SECRET", "ADMIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        load_settings(_env_file=None)
