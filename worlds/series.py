"""Game win probability -> series win probability, treating games as independent."""
import numpy as np


def p_series(p, best_of: int):
    """P(win a best-of-n series) given per-game win probability p."""
    p = np.asarray(p, float)
    if best_of == 1:
        return p
    if best_of == 3:
        return p**2 * (3 - 2 * p)
    if best_of == 5:
        return p**3 * (10 - 15 * p + 6 * p**2)
    raise ValueError(f"unsupported best_of={best_of}")
