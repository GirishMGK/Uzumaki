"""form26as — parse TRACES Form 26AS files (text/Excel/HTML/PDF) into a flat, searchable table."""

from .parser import Transaction, parse_transactions
from .loader import load_grid

__all__ = ["Transaction", "parse_transactions", "load_grid"]
__version__ = "0.1.0"
