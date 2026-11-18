"""Расчёт долей трафика между офферами потока.

Правила (как в редакторе из видео):
- закреплённые (pinned) доли не меняются;
- остаток до 100% делится поровну между незакреплёнными;
- доли целые: Keitaro принимает share только целым числом, поэтому лишние
  проценты от деления получают первые офферы по порядку (3 оффера -> 34/33/33).
"""

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

TOTAL = 100


class ShareError(ValueError):
    """Доли невозможно распределить. Текст показываем пользователю."""


@dataclass(frozen=True)
class Slot:
    key: Hashable
    pinned: bool = False
    share: int = 0  # учитывается только у закреплённых


def allocate(slots: Sequence[Slot], total: int = TOTAL) -> dict[Hashable, int]:
    """Возвращает долю для каждого слота. Порядок slots задаёт, кто получит лишний процент."""
    pinned = [s for s in slots if s.pinned]
    free = [s for s in slots if not s.pinned]

    for slot in pinned:
        if not 0 <= slot.share <= total:
            raise ShareError(f"Доля должна быть от 0 до {total}%, получено {slot.share}%")
    pinned_sum = sum(s.share for s in pinned)
    if pinned_sum > total:
        raise ShareError(f"Закреплённые доли в сумме дают {pinned_sum}%, это больше {total}%")

    result: dict[Hashable, int] = {s.key: s.share for s in pinned}
    if free:
        base, extra = divmod(total - pinned_sum, len(free))
        for index, slot in enumerate(free):
            result[slot.key] = base + (1 if index < extra else 0)
    return result


def total_problem(shares: Sequence[int], total: int = TOTAL) -> str | None:
    """Почему такие доли нельзя публиковать, или None, если всё в порядке."""
    if not shares:
        return "В потоке не осталось активных офферов"
    current = sum(shares)
    if current != total:
        return (
            f"Сумма долей {current}%, а должна быть {total}%. "
            "Открепите один из офферов или поправьте закреплённые доли"
        )
    return None
