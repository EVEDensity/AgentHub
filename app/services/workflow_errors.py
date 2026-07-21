class WorkflowVersionConflict(ValueError):
    def __init__(self, expected_version: int, current_version: int) -> None:
        super().__init__("Workflow was modified by another editor")
        self.expected_version = expected_version
        self.current_version = current_version
