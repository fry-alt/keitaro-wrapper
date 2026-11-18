import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.shares import ShareError, Slot, allocate, total_problem


def free(n: int) -> list[Slot]:
    return [Slot(key=i) for i in range(n)]


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, [100]),
        (2, [50, 50]),
        (3, [34, 33, 33]),  # как в видео: лишний процент первому
        (4, [25, 25, 25, 25]),
        (6, [17, 17, 17, 17, 16, 16]),
        (7, [15, 15, 14, 14, 14, 14, 14]),
    ],
)
def test_even_split(count, expected):
    assert list(allocate(free(count)).values()) == expected


def test_pinned_share_is_kept():
    # сценарий из видео: 25% закреплено, остальные двое делят 75%
    slots = [Slot("a"), Slot("pinned", pinned=True, share=25), Slot("b")]
    assert allocate(slots) == {"pinned": 25, "a": 38, "b": 37}


def test_all_pinned_returns_pinned_as_is():
    slots = [Slot("a", pinned=True, share=30), Slot("b", pinned=True, share=30)]
    assert allocate(slots) == {"a": 30, "b": 30}


def test_pinned_over_100_is_rejected():
    with pytest.raises(ShareError, match="110%"):
        allocate([Slot("a", True, 60), Slot("b", True, 50), Slot("c")])


@pytest.mark.parametrize("bad", [-1, 101])
def test_pinned_out_of_range(bad):
    with pytest.raises(ShareError):
        allocate([Slot("a", True, bad)])


def test_empty():
    assert allocate([]) == {}


def test_total_problem():
    assert total_problem([50, 50]) is None
    assert "60%" in total_problem([30, 30])
    assert "не осталось" in total_problem([])


slot_lists = st.lists(
    st.tuples(st.booleans(), st.integers(min_value=0, max_value=100)),
    min_size=1,
    max_size=30,
)


@given(slot_lists)
def test_properties(raw):
    slots = [Slot(key=i, pinned=p, share=s) for i, (p, s) in enumerate(raw)]
    pinned_sum = sum(s.share for s in slots if s.pinned)
    free_slots = [s for s in slots if not s.pinned]

    if pinned_sum > 100:
        with pytest.raises(ShareError):
            allocate(slots)
        return

    result = allocate(slots)
    assert set(result) == {s.key for s in slots}
    assert all(isinstance(v, int) and v >= 0 for v in result.values())
    # закреплённые не тронуты
    assert all(result[s.key] == s.share for s in slots if s.pinned)
    if free_slots:
        assert sum(result.values()) == 100
        free_values = [result[s.key] for s in free_slots]
        # делим поровну: разница не больше 1, и большие доли идут первыми
        assert max(free_values) - min(free_values) <= 1
        assert free_values == sorted(free_values, reverse=True)
