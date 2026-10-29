"""Shared generated-order arrival schedules; all internal times are hours."""
from math import isfinite
from random import Random


def validate_arrivals(interval, distribution='fixed', seed=None):
    interval = float(interval)
    distribution = str(distribution).lower()
    if distribution not in ('fixed', 'exponential'):
        raise ValueError('Arrival distribution must be Fixed or Exponential.')
    if not isfinite(interval) or interval < 0 or (distribution == 'exponential' and interval == 0):
        raise ValueError('Arrival interval must be non-negative; exponential mean interval must be positive.')
    if seed is not None and seed != '':
        try:
            parsed_seed = int(seed)
        except (ValueError, TypeError, OverflowError):
            raise ValueError('Arrival seed must be a whole number or blank.') from None
        if isinstance(seed, bool) or str(seed).strip() != str(parsed_seed):
            raise ValueError('Arrival seed must be a whole number or blank.')
        seed = parsed_seed
    else:
        seed = None
    return interval, distribution, seed


def arrival_offsets(count, interval, distribution='fixed', seed=None):
    """Start the first order at zero, then sample independent interarrival gaps."""
    interval, distribution, seed = validate_arrivals(interval, distribution, seed)
    rng = Random(seed)
    offsets = []
    elapsed = 0.0
    for index in range(count):
        if index:
            elapsed += rng.expovariate(1 / interval) if distribution == 'exponential' else interval
        offsets.append(elapsed)
    return offsets
