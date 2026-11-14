"""What-if studies: run to a precision, compare changes fairly."""
from raptor.api.scenarios.runner import Comparison, Scenario, compare
from raptor.api.scenarios.stopping import Precision, PrecisionRun, run_to_precision

__all__ = ["Comparison", "Precision", "PrecisionRun", "Scenario", "compare",
           "run_to_precision"]
