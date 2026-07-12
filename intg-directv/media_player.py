"""
Media-player entity for a DirecTV receiver or Genie client.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any, Final

import ucapi
from const import SIMPLE_COMMAND_KEYS, DirecTVConfig, LocationConfig, SimpleCommands
from receiver import DirecTVReceiver
from shef import SHEFError
from ucapi import EntityTypes, media_player
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features
from ucapi_framework import create_entity_id
from ucapi_framework.entities import MediaPlayerEntity

_LOG = logging.getLogger(__name__)

# No volume, mute or sound-mode features: SHEF has no audio control whatsoever. The
# TV or AVR downstream of the receiver owns volume.
FEATURES: Final[list[Features]] = [
    Features.ON_OFF,
    Features.TOGGLE,
    Features.PLAY_PAUSE,
    Features.STOP,
    Features.FAST_FORWARD,
    Features.REWIND,
    Features.NEXT,
    Features.PREVIOUS,
    Features.RECORD,
    Features.CHANNEL_SWITCHER,
    Features.DPAD,
    Features.NUMPAD,
    Features.HOME,
    Features.MENU,
    Features.GUIDE,
    Features.INFO,
    Features.COLOR_BUTTONS,
    Features.SELECT_SOURCE,
    Features.MEDIA_TITLE,
    Features.MEDIA_ARTIST,
    Features.MEDIA_ALBUM,
    Features.MEDIA_TYPE,
    Features.MEDIA_DURATION,
    Features.MEDIA_POSITION,
]

# Media-player commands that map onto a single SHEF key.
COMMAND_KEYS: Final[dict[str, str]] = {
    Commands.ON: "poweron",
    Commands.OFF: "poweroff",
    Commands.TOGGLE: "power",
    Commands.STOP: "stop",
    Commands.FAST_FORWARD: "ffwd",
    Commands.REWIND: "rew",
    # DirecTV has no track concept: next/previous jump forward 30s and back 6s, which
    # is what the physical remote's advance and replay keys do.
    Commands.NEXT: "advance",
    Commands.PREVIOUS: "replay",
    Commands.RECORD: "record",
    Commands.CHANNEL_UP: "chanup",
    Commands.CHANNEL_DOWN: "chandown",
    Commands.CURSOR_UP: "up",
    Commands.CURSOR_DOWN: "down",
    Commands.CURSOR_LEFT: "left",
    Commands.CURSOR_RIGHT: "right",
    Commands.CURSOR_ENTER: "select",
    Commands.BACK: "back",
    Commands.HOME: "menu",
    Commands.MENU: "menu",
    Commands.GUIDE: "guide",
    Commands.INFO: "info",
    Commands.FUNCTION_RED: "red",
    Commands.FUNCTION_GREEN: "green",
    Commands.FUNCTION_YELLOW: "yellow",
    Commands.FUNCTION_BLUE: "blue",
    Commands.DIGIT_0: "0",
    Commands.DIGIT_1: "1",
    Commands.DIGIT_2: "2",
    Commands.DIGIT_3: "3",
    Commands.DIGIT_4: "4",
    Commands.DIGIT_5: "5",
    Commands.DIGIT_6: "6",
    Commands.DIGIT_7: "7",
    Commands.DIGIT_8: "8",
    Commands.DIGIT_9: "9",
}


class DirecTVMediaPlayer(MediaPlayerEntity):
    """Media-player entity for one DirecTV location."""

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
            EntityTypes.MEDIA_PLAYER, config_device.identifier, location.client_addr
        )
        _LOG.debug("Initializing media player entity: %s", entity_id)

        super().__init__(
            entity_id,
            location.name,
            FEATURES,
            attributes={
                Attributes.STATE: device.location_state(location.client_addr),
                Attributes.SOURCE: "",
                Attributes.SOURCE_LIST: device.source_list,
            },
            device_class=DeviceClasses.SET_TOP_BOX,
            options={
                media_player.Options.SIMPLE_COMMANDS: [
                    cmd.value for cmd in SimpleCommands
                ]
            },
            cmd_handler=self.command_handler,
        )

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync entity state from the device after a poll or reconnect."""
        if self._device is None:
            self.set_unavailable()
            return

        attributes = self._device.get_media_player_attributes(self._client_addr)
        if attributes is not None:
            self.update(attributes)

    async def command_handler(
        self,
        entity: MediaPlayerEntity,
        cmd_id: str,
        params: dict[str, Any] | None,
    ) -> ucapi.StatusCodes:
        """Handle a media-player command."""
        _LOG.info("Got %s command request: %s %s", entity.id, cmd_id, params or "")

        if self._device is None:
            _LOG.warning("No receiver instance for entity: %s", self.id)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        try:
            if key := COMMAND_KEYS.get(cmd_id):
                await self._device.send_key(key, self._client_addr)
                return ucapi.StatusCodes.OK

            if simple_key := SIMPLE_COMMAND_KEYS.get(cmd_id):
                await self._device.send_key(simple_key, self._client_addr)
                return ucapi.StatusCodes.OK

            match cmd_id:
                case Commands.PLAY_PAUSE:
                    await self._device.toggle_play_pause(self._client_addr)
                case Commands.SELECT_SOURCE:
                    source = (params or {}).get("source")
                    if not source:
                        return ucapi.StatusCodes.BAD_REQUEST
                    await self._device.select_source(source, self._client_addr)
                case _:
                    _LOG.warning("Unsupported command: %s", cmd_id)
                    return ucapi.StatusCodes.NOT_IMPLEMENTED

        except SHEFError as err:
            _LOG.error("Error executing command %s: %s", cmd_id, err)
            return ucapi.StatusCodes.SERVER_ERROR

        return ucapi.StatusCodes.OK
