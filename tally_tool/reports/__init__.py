"""Report/transform layer for Tally-sourced data.

Everything in this package is a pure function over rows/DataFrames already
produced by tally_connector.py (live) or extract_ledgers.py (file export) --
nothing in here talks to Tally's XML server or touches a file on disk beyond
what's handed to it. That split keeps this package unit-testable with plain
fixtures, independent of whether the data came from a live pull or an
uploaded export.
"""
