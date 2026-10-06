class ToolExecutionError(RuntimeError):
    """Stable tool failure type; transport details remain outside public output."""
    def __init__(self, code):
        self.error_type = code
        super().__init__(code)
