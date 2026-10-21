import pytest
from pydantic import ValidationError

from app.domain.session import SessionOptions


@pytest.mark.parametrize("value", [0, -1, 1.5, "3000", True, None])
def test_silence_timeout_rejects_non_positive_or_non_integer_values(value: object) -> None:
    with pytest.raises(ValidationError):
        SessionOptions.model_validate({"silence_timeout_ms": value})


def test_silence_timeout_accepts_integer_milliseconds() -> None:
    assert SessionOptions(silence_timeout_ms=1250).silence_timeout_ms == 1250
