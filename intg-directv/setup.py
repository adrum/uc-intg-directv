"""
Setup flow for the DirecTV integration.

DirecTV has no usable discovery mechanism, so setup is manual entry of the receiver's
IP address. It then runs a second screen: the receiver is asked which locations it
serves — itself plus any Genie clients — and the user picks the ones to control.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import (
    DEFAULT_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    DirecTVConfig,
    LocationConfig,
)
from shef import (
    DEFAULT_PORT,
    SHEFAccessRestricted,
    SHEFClient,
    SHEFConnectionError,
    SHEFError,
    Location,
)
from ucapi import (
    IntegrationSetupError,
    RequestUserInput,
    SetupError,
    UserDataResponse,
)
from ucapi_framework import BaseSetupFlow

_LOG = logging.getLogger(__name__)


class DirecTVSetupFlow(BaseSetupFlow[DirecTVConfig]):
    """Manual-entry setup flow with a follow-up location selection screen."""

    def get_manual_entry_form(self) -> RequestUserInput:
        """Return the first screen: how to reach the receiver."""
        return RequestUserInput(
            {"en": "DirecTV Setup"},
            [
                {
                    "id": "info",
                    "label": {"en": "Setup Information"},
                    "field": {
                        "label": {
                            "value": {
                                "en": (
                                    "Enter the IP address of your DirecTV receiver. On the "
                                    "receiver, External Access must be enabled under "
                                    "Menu > Settings > Whole-Home > External Device, and the "
                                    "receiver should have a static IP address."
                                )
                            }
                        }
                    },
                },
                {
                    "id": "address",
                    "label": {"en": "IP Address"},
                    "field": {"text": {"value": ""}},
                },
                {
                    "id": "favorite_channels",
                    "label": {"en": "Favorite Channels"},
                    "field": {"text": {"value": ""}},
                },
                {
                    "id": "favorites_info",
                    "label": {"en": "About favorite channels"},
                    "field": {
                        "label": {
                            "value": {
                                "en": (
                                    "Optional, comma separated, e.g. 202, 206, 231-1. DirecTV "
                                    "offers no channel lineup over the network, so these become "
                                    "the media player's source list. Channel names are looked up "
                                    "from the receiver automatically."
                                )
                            }
                        }
                    },
                },
                {
                    "id": "poll_interval",
                    "label": {"en": "Poll Interval (seconds)"},
                    "field": {
                        "number": {
                            "value": DEFAULT_POLL_INTERVAL,
                            "min": MIN_POLL_INTERVAL,
                            "max": 300,
                            "steps": 5,
                        }
                    },
                },
            ],
        )

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> DirecTVConfig | SetupError | RequestUserInput:
        """
        Contact the receiver, then ask which of its locations to control.

        The config built here is stashed on ``_pending_device_config`` so the location
        screen's response can complete it.
        """
        address = str(input_values.get("address", "")).strip()
        if not address:
            return self.get_manual_entry_form()

        poll_interval = max(
            MIN_POLL_INTERVAL,
            _as_int(input_values.get("poll_interval"), DEFAULT_POLL_INTERVAL),
        )
        favorite_channels = _parse_channels(input_values.get("favorite_channels", ""))

        _LOG.debug("Connecting to DirecTV receiver at %s", address)

        try:
            async with SHEFClient(address, DEFAULT_PORT) as client:
                version = await client.get_version()
                locations = await client.get_locations()

        except SHEFAccessRestricted:
            _LOG.error("Receiver at %s rejected the request with 403", address)
            return SetupError(IntegrationSetupError.AUTHORIZATION_ERROR)
        except SHEFConnectionError as err:
            _LOG.error("Could not reach receiver at %s: %s", address, err)
            return SetupError(IntegrationSetupError.CONNECTION_REFUSED)
        except SHEFError as err:
            _LOG.error("Setup error for receiver at %s: %s", address, err)
            return SetupError(IntegrationSetupError.OTHER)

        if not version.receiver_id:
            _LOG.error("Receiver at %s did not report a receiver id", address)
            return SetupError(IntegrationSetupError.OTHER)

        if self._add_mode and self.config.contains(version.receiver_id):
            _LOG.warning("Receiver %s is already configured", version.receiver_id)
            return SetupError(IntegrationSetupError.OTHER)

        if not locations:
            _LOG.error("Receiver at %s reported no locations", address)
            return SetupError(IntegrationSetupError.OTHER)

        self._pending_device_config = DirecTVConfig(
            identifier=version.receiver_id,
            name=_receiver_name(locations),
            address=address,
            port=DEFAULT_PORT,
            locations=[],
            favorite_channels=favorite_channels,
            poll_interval=poll_interval,
        )

        return _location_screen(locations)

    async def handle_additional_configuration_response(
        self, msg: UserDataResponse
    ) -> DirecTVConfig | SetupError | None:
        """Record which locations the user selected."""
        config = self._pending_device_config
        if config is None:
            return SetupError(IntegrationSetupError.OTHER)

        selected: list[LocationConfig] = []
        for key, value in msg.input_values.items():
            if not key.startswith(_LOCATION_PREFIX) or not _as_bool(value):
                continue

            client_addr = key.removeprefix(_LOCATION_PREFIX)
            selected.append(
                LocationConfig(
                    client_addr=client_addr,
                    name=msg.input_values.get(
                        f"{_NAME_PREFIX}{client_addr}", client_addr
                    ),
                )
            )

        if not selected:
            _LOG.error("No locations were selected")
            return SetupError(IntegrationSetupError.OTHER)

        config.locations = selected
        _LOG.info(
            "Configured receiver %s with locations: %s",
            config.identifier,
            ", ".join(location.name for location in selected),
        )
        return config


_LOCATION_PREFIX = "location_"
_NAME_PREFIX = "name_"


def _location_screen(locations: list[Location]) -> RequestUserInput:
    """Build the location selection screen."""
    settings: list[dict[str, Any]] = [
        {
            "id": "locations_info",
            "label": {"en": "Locations"},
            "field": {
                "label": {
                    "value": {
                        "en": (
                            "Select the receiver and Genie clients to control. Each one "
                            "gets its own media player, remote, channel select, and sensors."
                        )
                    }
                }
            },
        }
    ]

    for location in locations:
        label = location.name
        if location.is_client:
            label = f"{location.name} (Genie client)"

        settings.append(
            {
                "id": f"{_LOCATION_PREFIX}{location.client_addr}",
                "label": {"en": label},
                "field": {"checkbox": {"value": True}},
            }
        )
        # Carry the name across the screen boundary so the response handler can label
        # the entities without contacting the receiver again.
        settings.append(
            {
                "id": f"{_NAME_PREFIX}{location.client_addr}",
                "label": {"en": f"{label} name"},
                "field": {"text": {"value": location.name}},
            }
        )

    return RequestUserInput({"en": "Select Locations"}, settings)


def _receiver_name(locations: list[Location]) -> str:
    """Name the receiver after its host location."""
    for location in locations:
        if not location.is_client:
            return f"DirecTV {location.name}"
    return "DirecTV Receiver"


def _parse_channels(value: Any) -> list[str]:
    """Parse the comma-separated favorite channel list, discarding anything unparseable."""
    channels: list[str] = []
    for part in str(value or "").split(","):
        channel = part.strip()
        if not channel:
            continue
        # Keep only channels the SHEF tune call can actually express.
        if all(segment.isdigit() for segment in channel.replace(".", "-").split("-")):
            channels.append(channel)
        else:
            _LOG.warning("Ignoring invalid favorite channel: %s", channel)
    return channels


def _as_int(value: Any, default: int) -> int:
    """Coerce a form value to an int."""
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    """Coerce a checkbox value to a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")
