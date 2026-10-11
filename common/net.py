"""Networking helpers for the backends Uzumaki runs in-process (HRM, Loan
Analytics)."""

from __future__ import annotations

import socket


def pick_free_port(host: str = "127.0.0.1") -> int:
    """A port the OS currently has free on `host`. Used instead of a fixed
    port so a second copy of the app, or anything else already listening,
    can't collide with -- or impersonate -- an embedded backend."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]
