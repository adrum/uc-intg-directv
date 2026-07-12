"""
Remote entity for a DirecTV receiver or Genie client.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from typing import Any, Final

from const import KEY_COMMANDS, DirecTVConfig, LocationConfig
from media_player import COMMAND_KEYS
from receiver import DirecTVReceiver
from shef import VALID_KEYS, SHEFError
from ucapi import EntityTypes, StatusCodes
from ucapi.media_player import States as MediaStates
from ucapi.remote import Attributes, Commands, Features, Remote
from ucapi.remote import States as RemoteStates
from ucapi.ui import (
    Buttons,
    EntityCommand,
    Size,
    UiPage,
    create_btn_mapping,
    create_ui_icon,
    create_ui_text,
)
from ucapi_framework import create_entity_id
from ucapi_framework.entities import RemoteEntity

_LOG = logging.getLogger(__name__)

STATE_MAPPING: Final[dict[MediaStates, RemoteStates]] = {
    MediaStates.UNKNOWN: RemoteStates.UNKNOWN,
    MediaStates.UNAVAILABLE: RemoteStates.UNAVAILABLE,
    MediaStates.OFF: RemoteStates.OFF,
    MediaStates.STANDBY: RemoteStates.OFF,
    MediaStates.ON: RemoteStates.ON,
    MediaStates.PLAYING: RemoteStates.ON,
    MediaStates.PAUSED: RemoteStates.ON,
}


def _cmd(command: str, repeat: int = 1) -> EntityCommand:
    """Build a remote.send command for a UI item or physical button."""
    return EntityCommand(Commands.SEND_CMD, {"command": command, "repeat": repeat})


BUTTON_MAPPING: Final[list] = [
    create_btn_mapping(Buttons.POWER, _cmd("power")),
    create_btn_mapping(Buttons.BACK, _cmd("back"), _cmd("exit")),
    create_btn_mapping(Buttons.HOME, _cmd("menu")),
    create_btn_mapping(Buttons.MENU, _cmd("list")),
    create_btn_mapping(Buttons.DPAD_UP, _cmd("up")),
    create_btn_mapping(Buttons.DPAD_DOWN, _cmd("down")),
    create_btn_mapping(Buttons.DPAD_LEFT, _cmd("left")),
    create_btn_mapping(Buttons.DPAD_RIGHT, _cmd("right")),
    create_btn_mapping(Buttons.DPAD_MIDDLE, _cmd("select")),
    create_btn_mapping(Buttons.CHANNEL_UP, _cmd("chanup")),
    create_btn_mapping(Buttons.CHANNEL_DOWN, _cmd("chandown")),
    create_btn_mapping(Buttons.PREV, _cmd("replay")),
    create_btn_mapping(Buttons.PLAY, _cmd("play"), _cmd("pause")),
    create_btn_mapping(Buttons.NEXT, _cmd("advance")),
    create_btn_mapping(Buttons.STOP, _cmd("stop")),
    create_btn_mapping(Buttons.RECORD, _cmd("record")),
    create_btn_mapping(Buttons.RED, _cmd("red")),
    create_btn_mapping(Buttons.GREEN, _cmd("green")),
    create_btn_mapping(Buttons.YELLOW, _cmd("yellow")),
    create_btn_mapping(Buttons.BLUE, _cmd("blue")),
]

UI_PAGES: Final[list[UiPage]] = [
    UiPage(
        page_id="directv_nav",
        name="DirecTV",
        grid=Size(4, 6),
        items=[
            create_ui_icon("uc:power-on", 0, 0, cmd=_cmd("poweron")),
            create_ui_text("Guide", 1, 0, size=Size(2, 1), cmd=_cmd("guide")),
            create_ui_icon("uc:power-off", 3, 0, cmd=_cmd("poweroff")),
            create_ui_icon("uc:up-arrow", 1, 1, size=Size(2, 1), cmd=_cmd("up")),
            create_ui_icon("uc:left-arrow", 0, 2, cmd=_cmd("left")),
            create_ui_text("OK", 1, 2, size=Size(2, 1), cmd=_cmd("select")),
            create_ui_icon("uc:right-arrow", 3, 2, cmd=_cmd("right")),
            create_ui_icon("uc:down-arrow", 1, 3, size=Size(2, 1), cmd=_cmd("down")),
            create_ui_icon("uc:back", 0, 4, cmd=_cmd("back")),
            create_ui_text("Exit", 1, 4, cmd=_cmd("exit")),
            create_ui_text("Info", 2, 4, cmd=_cmd("info")),
            create_ui_text("List", 3, 4, cmd=_cmd("list")),
            create_ui_text("CH +", 0, 5, cmd=_cmd("chanup")),
            create_ui_text("CH -", 1, 5, cmd=_cmd("chandown")),
            create_ui_text("Prev", 2, 5, cmd=_cmd("prev")),
            create_ui_text("Active", 3, 5, cmd=_cmd("active")),
        ],
    ),
    UiPage(
        page_id="directv_transport",
        name="Playback",
        grid=Size(4, 6),
        items=[
            create_ui_icon("uc:rewind", 0, 0, cmd=_cmd("rew")),
            create_ui_icon("uc:play", 1, 0, cmd=_cmd("play")),
            create_ui_icon("uc:pause", 2, 0, cmd=_cmd("pause")),
            create_ui_icon("uc:ff", 3, 0, cmd=_cmd("ffwd")),
            create_ui_text("Replay", 0, 1, size=Size(2, 1), cmd=_cmd("replay")),
            create_ui_text("Advance", 2, 1, size=Size(2, 1), cmd=_cmd("advance")),
            create_ui_text("Stop", 0, 2, size=Size(2, 1), cmd=_cmd("stop")),
            create_ui_text("Record", 2, 2, size=Size(2, 1), cmd=_cmd("record")),
            create_ui_text("Format", 0, 3, size=Size(2, 1), cmd=_cmd("format")),
            create_ui_text("Dash", 2, 3, cmd=_cmd("dash")),
            create_ui_text("Enter", 3, 3, cmd=_cmd("enter")),
            create_ui_text("Red", 0, 4, cmd=_cmd("red")),
            create_ui_text("Green", 1, 4, cmd=_cmd("green")),
            create_ui_text("Yellow", 2, 4, cmd=_cmd("yellow")),
            create_ui_text("Blue", 3, 4, cmd=_cmd("blue")),
        ],
    ),
    UiPage(
        page_id="directv_numpad",
        name="Numpad",
        grid=Size(3, 4),
        items=[
            create_ui_text("1", 0, 0, cmd=_cmd("1")),
            create_ui_text("2", 1, 0, cmd=_cmd("2")),
            create_ui_text("3", 2, 0, cmd=_cmd("3")),
            create_ui_text("4", 0, 1, cmd=_cmd("4")),
            create_ui_text("5", 1, 1, cmd=_cmd("5")),
            create_ui_text("6", 2, 1, cmd=_cmd("6")),
            create_ui_text("7", 0, 2, cmd=_cmd("7")),
            create_ui_text("8", 1, 2, cmd=_cmd("8")),
            create_ui_text("9", 2, 2, cmd=_cmd("9")),
            create_ui_text("-", 0, 3, cmd=_cmd("dash")),
            create_ui_text("0", 1, 3, cmd=_cmd("0")),
            create_ui_text("Enter", 2, 3, cmd=_cmd("enter")),
        ],
    ),
]


class DirecTVRemote(RemoteEntity):
    """Remote entity for one DirecTV location."""

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
            EntityTypes.REMOTE, config_device.identifier, location.client_addr
        )
        _LOG.debug("Initializing remote entity: %s", entity_id)

        super().__init__(
            entity_id,
            f"{location.name} Remote",
            [Features.SEND_CMD, Features.ON_OFF, Features.TOGGLE],
            attributes={
                Attributes.STATE: self.map_entity_states(
                    device.location_state(location.client_addr)
                )
            },
            simple_commands=list(KEY_COMMANDS),
            button_mapping=BUTTON_MAPPING,
            ui_pages=UI_PAGES,
            cmd_handler=self.command_handler,
        )

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync entity state from the device after a poll or reconnect."""
        if self._device is None:
            self.set_unavailable()
            return

        self.update(
            {
                Attributes.STATE: self.map_entity_states(
                    self._device.location_state(self._client_addr)
                )
            }
        )

    def map_entity_states(self, device_state: MediaStates) -> RemoteStates:
        """Map the location's media-player state onto a remote state."""
        return STATE_MAPPING.get(device_state, RemoteStates.UNKNOWN)

    @staticmethod
    def _resolve_key(command: str) -> str | None:
        """
        Resolve a remote command to the SHEF key it sends.

        Accepts a simple command ("Guide"), a media-player command ("cursor_up"), or a
        raw SHEF key ("guide") — so activity sequences can use whichever is at hand.
        """
        if key := KEY_COMMANDS.get(command):
            return key
        if key := COMMAND_KEYS.get(command):
            return key
        if command.lower() in VALID_KEYS:
            return command.lower()
        return None

    @staticmethod
    def _int_param(params: dict[str, Any] | None, name: str, default: int) -> int:
        """Read an integer command parameter, tolerating string values."""
        value = (params or {}).get(name, default)
        if isinstance(value, str) and value:
            return int(float(value))
        if isinstance(value, (int, float)):
            return int(value)
        return default

    async def command_handler(
        self, entity: Remote, cmd_id: str, params: dict[str, Any] | None = None
    ) -> StatusCodes:
        """Handle a remote command."""
        _LOG.info("Got %s command request: %s %s", entity.id, cmd_id, params or "")

        if self._device is None:
            _LOG.warning("No receiver instance for entity: %s", self.id)
            return StatusCodes.SERVICE_UNAVAILABLE

        try:
            match cmd_id:
                case Commands.ON:
                    await self._device.send_key("poweron", self._client_addr)
                case Commands.OFF:
                    await self._device.send_key("poweroff", self._client_addr)
                case Commands.TOGGLE:
                    await self._device.send_key("power", self._client_addr)
                case Commands.SEND_CMD:
                    return await self._send_command(params)
                case Commands.SEND_CMD_SEQUENCE:
                    return await self._send_sequence(params)
                case _:
                    _LOG.warning("Unsupported command: %s", cmd_id)
                    return StatusCodes.NOT_IMPLEMENTED

        except SHEFError as err:
            _LOG.error("Error executing command %s: %s", cmd_id, err)
            return StatusCodes.SERVER_ERROR

        return StatusCodes.OK

    async def _send_command(self, params: dict[str, Any] | None) -> StatusCodes:
        """Send a single command, honouring the repeat and delay parameters."""
        command = (params or {}).get("command", "")
        key = self._resolve_key(command)
        if key is None:
            _LOG.warning("Unknown command: %s", command)
            return StatusCodes.BAD_REQUEST

        repeat = max(1, self._int_param(params, "repeat", 1))
        delay = self._int_param(params, "delay", 0)

        for index in range(repeat):
            await self._device.send_key(key, self._client_addr)
            if delay and index < repeat - 1:
                await asyncio.sleep(delay / 1000)

        return StatusCodes.OK

    async def _send_sequence(self, params: dict[str, Any] | None) -> StatusCodes:
        """Send a sequence of commands."""
        sequence = (params or {}).get("sequence", [])
        repeat = max(1, self._int_param(params, "repeat", 1))
        delay = self._int_param(params, "delay", 0)

        for _ in range(repeat):
            for command in sequence:
                key = self._resolve_key(command)
                if key is None:
                    _LOG.warning("Unknown command in sequence: %s", command)
                    return StatusCodes.BAD_REQUEST

                await self._device.send_key(key, self._client_addr)
                if delay:
                    await asyncio.sleep(delay / 1000)

        return StatusCodes.OK
