from __future__ import annotations

import re

import pytest

from vigente.ids import new_ulid

ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_has_ulid_format() -> None:
    assert ULID.match(new_ulid())


def test_sorts_by_creation_time() -> None:
    assert new_ulid(1_000) < new_ulid(2_000) < new_ulid(2**48 - 1)


def test_starts_with_the_encoded_timestamp() -> None:
    assert new_ulid(0).startswith("0000000000")


def test_is_unique_within_the_same_millisecond() -> None:
    assert len({new_ulid(5) for _ in range(1_000)}) == 1_000


@pytest.mark.parametrize("timestamp", [-1, 2**48])
def test_rejects_timestamps_out_of_range(timestamp: int) -> None:
    with pytest.raises(ValueError):
        new_ulid(timestamp)
