from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MessageKind = Literal["transient_ui", "operation_confirmation", "report", "alert", "export"]


@dataclass(frozen=True)
class BotMessageRef:
    chat_id: int
    message_id: int
    kind: MessageKind

    @property
    def is_persistent(self) -> bool:
        return self.kind in {"operation_confirmation", "report", "alert", "export"}


def should_cleanup(ref: BotMessageRef) -> bool:
    return not ref.is_persistent
