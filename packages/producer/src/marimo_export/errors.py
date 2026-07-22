from __future__ import annotations


class UnsupportedMarimoError(RuntimeError):
    pass


class UnsupportedProducerModeError(RuntimeError):
    pass


class InvalidPlanError(ValueError):
    pass


class ScenarioBuildError(RuntimeError):
    def __init__(self, scenario_id: str, cause: BaseException) -> None:
        self.scenario_id = scenario_id
        self.cause = cause
        self.cause_message = str(cause) or type(cause).__name__
        super().__init__(f"scenario {scenario_id!r} failed: {self.cause_message}")


class IntegrityError(RuntimeError):
    pass


class StorageError(OSError):
    pass
