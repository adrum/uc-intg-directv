"""
DirecTV receiver communication for the Remote Two/3 integration driver.

One instance represents one physical receiver. Every location the user selected during
setup — the receiver itself and any Genie clients hanging off it — is polled and
commanded through this single object, because all of them are addressed through the
host receiver's single-threaded HTTP server.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from asyncio import AbstractEventLoop
from dataclasses import dataclass
from datetime import datetime, timezone

from const import DirecTVConfig, LocationConfig
from shef import (
    HOST_CLIENT_ADDR,
    MODE_ACTIVE,
    Program,
    SHEFAccessRestricted,
    SHEFClient,
    SHEFError,
    format_channel,
    parse_channel,
)
from ucapi import media_player
from ucapi.select import States as SelectStates
from ucapi.sensor import States as SensorStates
from ucapi_framework import BaseIntegrationDriver, PollingDevice
from ucapi_framework.helpers import (
    MediaPlayerAttributes,
    RemoteAttributes,
    SelectAttributes,
    SensorAttributes,
)

_LOG = logging.getLogger(__name__)


@dataclass
class LocationState:
    """Live state of a single location, refreshed on every poll."""

    state: media_player.States = media_player.States.UNKNOWN
    program: Program | None = None
    paused: bool = False
    """Tracked optimistically: SHEF cannot report whether playback is paused."""


class DirecTVReceiver(PollingDevice):
    """A DirecTV receiver and the Genie clients attached to it."""

    def __init__(
        self,
        device_config: DirecTVConfig,
        loop: AbstractEventLoop | None = None,
        config_manager=None,
        driver: BaseIntegrationDriver | None = None,
    ) -> None:
        """Create instance."""
        super().__init__(
            device_config,
            loop,
            poll_interval=device_config.poll_interval,
            config_manager=config_manager,
            driver=driver,
        )
        self._client = SHEFClient(device_config.address, device_config.port)

        self._locations: dict[str, LocationState] = {
            location.client_addr: LocationState()
            for location in device_config.locations
        }

        # Favorite channels, presented to the media player as its source list. The
        # labels are resolved to callsigns on connect; until then they are bare
        # channel numbers.
        self._source_list: list[str] = list(device_config.favorite_channels)
        self._channel_by_source: dict[str, str] = {
            channel: channel for channel in device_config.favorite_channels
        }

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def identifier(self) -> str:
        """Return the receiver identifier."""
        if not self._device_config.identifier:
            raise ValueError("Instance not initialized, no identifier available")
        return self._device_config.identifier

    @property
    def name(self) -> str:
        """Return the receiver name."""
        return self._device_config.name

    @property
    def address(self) -> str | None:
        """Return the receiver IP address."""
        return self._device_config.address

    @property
    def log_id(self) -> str:
        """Return a log identifier."""
        return self._device_config.name or self._device_config.identifier

    @property
    def locations(self) -> list[LocationConfig]:
        """Return the locations selected during setup."""
        return list(self._device_config.locations)

    @property
    def source_list(self) -> list[str]:
        """Return the favorite channels offered as media-player sources."""
        return list(self._source_list)

    def location_name(self, client_addr: str) -> str:
        """Return the friendly name of a location."""
        for location in self._device_config.locations:
            if location.client_addr == client_addr:
                return location.name
        return self.name

    # ── State accessors ──────────────────────────────────────────────────────

    def location_state(self, client_addr: str) -> media_player.States:
        """Return the power/play state of a location."""
        location = self._locations.get(client_addr)
        return location.state if location else media_player.States.UNKNOWN

    def program(self, client_addr: str) -> Program | None:
        """Return the program currently tuned at a location, if any."""
        location = self._locations.get(client_addr)
        return location.program if location else None

    def _current_source(self, program: Program | None) -> str:
        """Return the source-list label matching the tuned channel, if it is a favorite."""
        if program is None:
            return ""
        for source, channel in self._channel_by_source.items():
            if parse_channel(channel) == parse_channel(program.channel):
                return source
        return ""

    def get_media_player_attributes(
        self, client_addr: str
    ) -> MediaPlayerAttributes | None:
        """Return current media-player attributes for a location."""
        if client_addr not in self._locations:
            return None

        state = self.location_state(client_addr)
        program = self.program(client_addr)

        if program is None:
            return MediaPlayerAttributes(
                STATE=state,
                SOURCE="",
                SOURCE_LIST=self._source_list,
                MEDIA_TITLE="",
                MEDIA_ARTIST="",
                MEDIA_ALBUM="",
                MEDIA_DURATION=0,
                MEDIA_POSITION=0,
            )

        if program.music_title:
            media_type = "MUSIC"
            title = program.music_title
            artist = program.music_artist or ""
            album = program.music_album or ""
        else:
            media_type = "TVSHOW" if program.episode_title else "MOVIE"
            title = program.title
            # There is no artist concept on a set-top box, so the second line carries
            # the episode title, falling back to the channel it is playing on.
            artist = program.episode_title or self._channel_label(program)
            album = ""

        return MediaPlayerAttributes(
            STATE=state,
            SOURCE=self._current_source(program),
            SOURCE_LIST=self._source_list,
            MEDIA_TYPE=media_type,
            MEDIA_TITLE=title,
            MEDIA_ARTIST=artist,
            MEDIA_ALBUM=album,
            MEDIA_DURATION=program.duration,
            MEDIA_POSITION=program.offset,
            MEDIA_POSITION_UPDATED_AT=datetime.now(timezone.utc).isoformat(),
        )

    def get_remote_attributes(self, client_addr: str) -> RemoteAttributes | None:
        """Return current remote attributes for a location.

        The remote entity maps the device state itself, so no state is carried here.
        """
        if client_addr not in self._locations:
            return None
        return RemoteAttributes(STATE=None)

    def get_sensor_attributes(
        self, client_addr: str, sensor_id: str
    ) -> SensorAttributes | None:
        """Return current attributes of one sensor at a location."""
        if client_addr not in self._locations:
            return None

        program = self.program(client_addr)
        if program is None or self.location_state(client_addr) in (
            media_player.States.UNAVAILABLE,
            media_player.States.UNKNOWN,
        ):
            return SensorAttributes(STATE=SensorStates.UNAVAILABLE, VALUE="")

        values: dict[str, str] = {
            "channel": program.channel,
            "callsign": program.callsign,
            "title": program.title,
            "episode_title": program.episode_title or "",
            "rating": program.rating or "",
            "recording": "Yes" if program.is_recording else "No",
        }

        if sensor_id not in values:
            return None

        return SensorAttributes(STATE=SensorStates.ON, VALUE=values[sensor_id])

    def get_select_attributes(self, client_addr: str) -> SelectAttributes | None:
        """Return current favorite-channel select attributes for a location."""
        if client_addr not in self._locations:
            return None

        active = self.location_state(client_addr) == media_player.States.PLAYING
        return SelectAttributes(
            STATE=SelectStates.ON if active else SelectStates.UNAVAILABLE,
            CURRENT_OPTION=self._current_source(self.program(client_addr)),
            OPTIONS=self._source_list,
        )

    @staticmethod
    def _channel_label(program: Program) -> str:
        """Render a program's channel as "202 ESPN", or just the number if unnamed."""
        if program.callsign:
            return f"{program.channel} {program.callsign}"
        return program.channel

    # ── Connection lifecycle ─────────────────────────────────────────────────

    async def establish_connection(self) -> None:
        """
        Verify the receiver is reachable and label the favorite channels.

        Raising here makes PollingDevice.connect() report failure, so let SHEF errors
        propagate untouched.
        """
        version = await self._client.get_version()
        _LOG.info(
            "[%s] Connected to receiver %s (software %s, SHEF %s)",
            self.log_id,
            version.receiver_id,
            version.software_version,
            version.shef_version,
        )
        await self._resolve_source_list()

    async def disconnect(self) -> None:
        """Stop polling and release the HTTP session."""
        await super().disconnect()
        await self._client.close()

    async def _resolve_source_list(self) -> None:
        """
        Turn the configured channel numbers into "202 ESPN" style labels.

        getProgInfo reports a channel's callsign without tuning to it. A channel that
        the receiver does not carry simply keeps its bare number as its label.
        """
        if not self._device_config.favorite_channels:
            return

        sources: list[str] = []
        channel_by_source: dict[str, str] = {}

        for channel in self._device_config.favorite_channels:
            label = channel
            try:
                program = await self._client.get_prog_info(channel)
                if program.callsign:
                    major, minor = parse_channel(channel)
                    label = f"{format_channel(major, minor)} {program.callsign}"
            except SHEFError as err:
                _LOG.debug(
                    "[%s] Could not resolve callsign for channel %s: %s",
                    self.log_id,
                    channel,
                    err,
                )

            # Two favorites resolving to the same label would collide in the source
            # list, so keep the first and leave the duplicate as its bare number.
            if label in channel_by_source:
                label = channel

            sources.append(label)
            channel_by_source[label] = channel

        self._source_list = sources
        self._channel_by_source = channel_by_source
        _LOG.debug("[%s] Favorite channels: %s", self.log_id, sources)

    async def poll_device(self) -> None:
        """Refresh every selected location, then notify the entities once."""
        for client_addr in list(self._locations):
            await self._poll_location(client_addr)

        self.push_update()

    async def _poll_location(self, client_addr: str) -> None:
        """Refresh a single location."""
        location = self._locations[client_addr]

        try:
            mode = await self._client.get_mode(client_addr)
        except SHEFAccessRestricted:
            _LOG.warning(
                "[%s] External Access is disabled on the receiver", self.log_id
            )
            location.state = media_player.States.UNAVAILABLE
            location.program = None
            return
        except SHEFError as err:
            _LOG.debug("[%s] %s is unreachable: %s", self.log_id, client_addr, err)
            location.state = media_player.States.UNAVAILABLE
            location.program = None
            return

        if mode != MODE_ACTIVE:
            # A location in standby answers getTuned with an HTTP 500, so stop here.
            location.state = media_player.States.OFF
            location.program = None
            return

        try:
            program = await self._client.get_tuned(client_addr)
            # A channel change means playback resumed, whatever we assumed before.
            if location.program and location.program.channel != program.channel:
                location.paused = False
            location.program = program
            location.state = media_player.States.PLAYING
        except SHEFError as err:
            # Active but not reporting a program: the box is on but between states.
            _LOG.debug(
                "[%s] Could not read tuned program at %s: %s",
                self.log_id,
                client_addr,
                err,
            )
            location.state = media_player.States.ON
            location.program = None

    # ── Commands ─────────────────────────────────────────────────────────────

    async def send_key(
        self, key: str, client_addr: str = HOST_CLIENT_ADDR, hold: str | None = None
    ) -> None:
        """Send a remote key to a location."""
        _LOG.debug("[%s] Sending key %s to %s", self.log_id, key, client_addr)
        if hold:
            await self._client.press_key(key, client_addr, hold)
        else:
            await self._client.press_key(key, client_addr)

    async def tune(self, channel: str, client_addr: str = HOST_CLIENT_ADDR) -> None:
        """Tune a location to a channel."""
        _LOG.debug("[%s] Tuning %s to %s", self.log_id, client_addr, channel)
        await self._client.tune(channel, client_addr)
        self._set_paused(client_addr, False)

    async def toggle_play_pause(self, client_addr: str = HOST_CLIENT_ADDR) -> None:
        """
        Toggle play/pause.

        DirecTV exposes discrete play and pause keys but no toggle, and SHEF cannot
        report whether playback is paused. The paused flag is therefore tracked
        optimistically, and reset whenever the channel changes.
        """
        location = self._locations.get(client_addr)
        paused = location.paused if location else False

        await self.send_key("play" if paused else "pause", client_addr)
        self._set_paused(client_addr, not paused)

    def _set_paused(self, client_addr: str, paused: bool) -> None:
        """Record the assumed transport state of a location."""
        location = self._locations.get(client_addr)
        if location:
            location.paused = paused

    async def select_source(
        self, source: str, client_addr: str = HOST_CLIENT_ADDR
    ) -> None:
        """Tune a location to one of the favorite channels."""
        channel = self._channel_by_source.get(source)
        if channel is None:
            raise SHEFError(f"Unknown source: {source}")
        await self.tune(channel, client_addr)
