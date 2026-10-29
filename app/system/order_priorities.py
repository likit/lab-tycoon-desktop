"""Sample generated order priorities independently of arrivals and processing."""
from math import isfinite
from random import Random


def generated_priorities(count, probability=0, seed=None):
    probability = float(probability)
    if not isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('STAT order probability must be between 0 and 1.')
    rng = Random(f'stat:{seed}' if seed is not None else None)
    return ['urgent' if probability == 1 or (probability > 0 and rng.random() < probability)
            else 'routine' for _ in range(count)]
