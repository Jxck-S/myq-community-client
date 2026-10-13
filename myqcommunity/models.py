"""Lightweight data models for myQ Community resources.

Shapes mirror the API responses observed in the app's bundled demo fixtures
(``assets/premium-resident/*.json``). Unknown/extra fields are preserved in
``.raw`` so nothing is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import const


@dataclass
class Community:
    id: str  # "COMM-XXXX-..." — used as {accountId} in every other endpoint
    name: str
    active: bool = True
    timezone: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict) -> "Community":
        return cls(
            id=d["id"],
            name=d.get("name", ""),
            active=d.get("active", True),
            timezone=d.get("timezone"),
            raw=d,
        )


@dataclass
class Device:
    serial_number: str
    name: str
    family: str        # live API: device_family (e.g. "access_panel", "remote_lock")
    type: str          # live API: device_type (e.g. "access_panel_door")
    kind: str = ""     # live API: "door" for unlockable entry points
    online: bool | None = None
    zone: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict) -> "Device":
        # The live API is flat (device_family/device_type/name/online/kind).
        # The app's bundled demo fixtures nest under configuration/state — support both.
        cfg = d.get("configuration") or {}
        state = d.get("state") or {}
        online = d.get("online")
        if online is None:
            online = state.get("online")
        return cls(
            serial_number=d["serial_number"],
            name=d.get("name") or cfg.get("name") or cfg.get("description")
            or d.get("display_serial_number", ""),
            family=d.get("device_family") or d.get("family", ""),
            type=d.get("device_type") or d.get("type", ""),
            kind=d.get("kind", ""),
            online=online,
            zone=d.get("zone"),
            raw=d,
        )

    @property
    def is_lock(self) -> bool:
        """True if this device can be unlocked via the door-unlock endpoint."""
        return self.kind == "door" or self.family in const.LOCK_FAMILIES


@dataclass
class HistoryEvent:
    """One access-history entry (e.g. an unlock)."""

    id: str
    event: str            # e.g. "unlocked"
    device_name: str
    serial_number: str
    timestamp: str        # ISO-8601 UTC, e.g. "2026-10-13T04:21:07Z"
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict) -> "HistoryEvent":
        return cls(
            id=d.get("id", ""),
            event=d.get("event", ""),
            device_name=d.get("device_name", ""),
            serial_number=d.get("serial_number", ""),
            timestamp=d.get("timestamp", ""),
            raw=d,
        )
