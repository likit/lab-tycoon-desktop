"""Staff collection policy for specimen preparation, independent of arrivals."""
import math

DEFAULT_BATCH_SIZE = 5
DEFAULT_BATCH_WAIT_MINUTES = 1.0


def validate_batch_settings(size, wait_minutes):
    if isinstance(size, bool) or int(size) != size or size < 1:
        raise ValueError("Batch size must be a positive integer.")
    if not math.isfinite(wait_minutes) or wait_minutes < 0:
        raise ValueError("Batch wait must be a finite, non-negative number of minutes.")
    return int(size), float(wait_minutes)


def staff_batch_settings(node):
    if (node.kind == "equipment"
            or node.widget.metadata.get("node_type") != "specimens processing"
            or node.widget.required_staff <= 0 or node.widget.human_throughput <= 0):
        return 1, 0.0
    return validate_batch_settings(
        node.metadata.get("batch_size", DEFAULT_BATCH_SIZE),
        node.metadata.get("batch_wait_minutes", DEFAULT_BATCH_WAIT_MINUTES))
