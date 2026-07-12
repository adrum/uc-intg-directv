"""
DirecTV integration constants and configuration models.

Note: do not add `from __future__ import annotations` to this module. The framework's
config manager reflects on the dataclass field types at runtime to deserialize nested
dataclasses, and stringified annotations would break that.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from shef import HOST_CLIENT_ADDR

# The receiver is slow and single-threaded, and every poll costs 1-2 requests per
# location. 30s keeps now-playing reasonably fresh without hammering it.
DEFAULT_POLL_INTERVAL: Final[int] = 30
MIN_POLL_INTERVAL: Final[int] = 10


@dataclass
class LocationConfig:
    """A receiver or Genie client the user chose to control."""

    client_addr: str
    """SHEF clientAddr: "0" for the host receiver, else the client's MAC address."""
    name: str
    """Location name as reported by the receiver, e.g. "Living Room"."""

    @property
    def is_host(self) -> bool:
        """Return True if this is the host receiver rather than a Genie client."""
        return self.client_addr == HOST_CLIENT_ADDR


@dataclass
class DirecTVConfig:
    """DirecTV receiver configuration."""

    identifier: str
    """Unique identifier of the receiver (its SHEF receiverId)."""
    name: str
    """Friendly name of the receiver."""
    address: str
    """IP address of the receiver."""
    port: int = 8080
    """SHEF port. Always 8080 on retail hardware."""
    locations: list[LocationConfig] = field(default_factory=list)
    """Receiver and Genie clients selected during setup. Each gets its own entities."""
    favorite_channels: list[str] = field(default_factory=list)
    """Channel numbers offered as media-player sources, e.g. ["202", "206-1"]."""
    poll_interval: int = DEFAULT_POLL_INTERVAL
    """Seconds between status polls."""


@dataclass
class SensorConfig:
    """Definition of a sensor entity created per location."""

    identifier: str
    name: str
    default: str = ""


class SimpleCommands(str, Enum):
    """DirecTV keys that have no direct media-player equivalent."""

    LIST = "List"
    ACTIVE = "Active"
    EXIT = "Exit"
    FORMAT = "Format"
    REPLAY = "Replay"
    ADVANCE = "Advance"
    # DirecTV has discrete play and pause keys and no toggle key, so both are offered
    # alongside the media player's optimistic PLAY_PAUSE toggle.
    PLAY = "Play"
    PAUSE = "Pause"
    DASH = "Dash"
    ENTER = "Enter"
    PREV_CHANNEL = "Previous Channel"


# Every key SHEF accepts, under a friendly name. The remote entity offers all of these
# as simple commands so that any DirecTV key can be bound in an activity; the media
# player only offers the SimpleCommands subset, since the rest duplicate its features.
KEY_COMMANDS: Final[dict[str, str]] = {
    "Power": "power",
    "Power On": "poweron",
    "Power Off": "poweroff",
    "Format": "format",
    "Play": "play",
    "Pause": "pause",
    "Stop": "stop",
    "Rewind": "rew",
    "Fast Forward": "ffwd",
    "Replay": "replay",
    "Advance": "advance",
    "Record": "record",
    "Guide": "guide",
    "Active": "active",
    "List": "list",
    "Exit": "exit",
    "Back": "back",
    "Menu": "menu",
    "Info": "info",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "Select": "select",
    "Red": "red",
    "Green": "green",
    "Yellow": "yellow",
    "Blue": "blue",
    "Channel Up": "chanup",
    "Channel Down": "chandown",
    "Previous Channel": "prev",
    "Digit 0": "0",
    "Digit 1": "1",
    "Digit 2": "2",
    "Digit 3": "3",
    "Digit 4": "4",
    "Digit 5": "5",
    "Digit 6": "6",
    "Digit 7": "7",
    "Digit 8": "8",
    "Digit 9": "9",
    "Dash": "dash",
    "Enter": "enter",
}

# Maps a media-player simple command onto the SHEF key it sends.
SIMPLE_COMMAND_KEYS: Final[dict[str, str]] = {
    command.value: KEY_COMMANDS[command.value] for command in SimpleCommands
}

# Sensors created for every selected location. Values are filled in from the tuned
# program on each poll.
SENSORS: Final[tuple[SensorConfig, ...]] = (
    SensorConfig(identifier="channel", name="Channel"),
    SensorConfig(identifier="callsign", name="Channel Name"),
    SensorConfig(identifier="title", name="Title"),
    SensorConfig(identifier="episode_title", name="Episode Title"),
    SensorConfig(identifier="rating", name="Rating"),
    SensorConfig(identifier="recording", name="Recording"),
)
