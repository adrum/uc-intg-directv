"""
Favorite-channel select entity for a DirecTV location.

SHEF has no channel-lineup endpoint, so the options are the favorite channels the user
entered during setup, labelled with the callsign the receiver reports for each.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import ucapi
from const import DirecTVConfig, LocationConfig
from receiver import DirecTVReceiver
from shef import SHEFError
from ucapi import EntityTypes, StatusCodes
from ucapi.select import Attributes, Commands, States
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SelectEntity

_LOG = logging.getLogger(__name__)


class DirecTVChannelSelect(SelectEntity):
    """Select entity that tunes a location to one of the favorite channels."""

    def __init__(
        self,
        config_device: DirecTVConfig,
        device: DirecTVReceiver,
        location: LocationConfig,
    ) -> None:
        """Initialize the entity."""
        self._device = device
        self._client_addr = location.client_addr

        entity_id = create_entity_id(
            EntityTypes.SELECT,
            config_device.identifier,
            f"{location.client_addr}_channel",
        )
        _LOG.debug("Initializing select entity: %s", entity_id)

        super().__init__(
            entity_id,
            f"{location.name} Channel",
            attributes={
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.CURRENT_OPTION: "",
                Attributes.OPTIONS: device.source_list,
            },
            cmd_handler=self.command_handler,
        )

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync entity state from the device after a poll or reconnect."""
        if self._device is None:
            self.set_unavailable()
            return

        attributes = self._device.get_select_attributes(self._client_addr)
        if attributes is not None:
            self.update(attributes)

    async def command_handler(
        self,
        entity: SelectEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
        _websocket: Any = None,
    ) -> ucapi.StatusCodes:
        """Handle a select command."""
        _LOG.info("Got %s command request: %s %s", entity.id, cmd_id, params or "")

        if self._device is None:
            _LOG.warning("No receiver instance for entity: %s", self.id)
            return StatusCodes.SERVICE_UNAVAILABLE

        options: list[str] = self.attributes.get(Attributes.OPTIONS, [])
        if not options:
            return StatusCodes.BAD_REQUEST

        option = self._resolve_option(cmd_id, params, options)
        if option is None:
            return StatusCodes.BAD_REQUEST

        try:
            await self._device.select_source(option, self._client_addr)
        except SHEFError as err:
            _LOG.error("Error selecting channel %s: %s", option, err)
            return StatusCodes.SERVER_ERROR

        return StatusCodes.OK

    def _resolve_option(
        self, cmd_id: str, params: dict[str, Any] | None, options: list[str]
    ) -> str | None:
        """Work out which channel a select command refers to."""
        current = self.attributes.get(Attributes.CURRENT_OPTION, "")
        cycle = bool((params or {}).get("cycle", False))

        match cmd_id:
            case Commands.SELECT_OPTION:
                option = (params or {}).get("option")
                return option if option in options else None
            case Commands.SELECT_FIRST:
                return options[0]
            case Commands.SELECT_LAST:
                return options[-1]
            case Commands.SELECT_NEXT | Commands.SELECT_PREVIOUS:
                step = 1 if cmd_id == Commands.SELECT_NEXT else -1
                # With nothing tuned to a favorite, step in from the nearest end.
                if current not in options:
                    return options[0] if step == 1 else options[-1]

                index = options.index(current) + step
                if 0 <= index < len(options):
                    return options[index]
                return options[index % len(options)] if cycle else None
            case _:
                _LOG.warning("Unsupported command: %s", cmd_id)
                return None
