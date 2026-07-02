"""form26as — parse a TRACES Form 26AS (Excel/HTML) into a flat, searchable table."""

from .parser import Transaction, parse_part_a
from .loader import load_grid

__all__ = ["Transaction", "parse_part_a", "load_grid"]
__version__ = "0.1.0"
