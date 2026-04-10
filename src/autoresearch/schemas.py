from dataclasses import dataclass


@dataclass(frozen=True)
class RunCreateRequest:
    run_kind: str
    project: str
    notes: str | None = None
