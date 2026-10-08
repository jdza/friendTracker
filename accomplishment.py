from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class Accomplishment:
    friend: str
    source: str
    type: str
    timestamp: datetime
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d
