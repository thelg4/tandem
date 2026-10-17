"""Interactions: derive rewrite, removal, exact-revert and overlap records (spec §7).

No intent or correctness inference (INT-01).
"""

from tandem.interactions.derive import Interaction, Window, derive, review_order

__all__ = ["Interaction", "Window", "derive", "review_order"]
