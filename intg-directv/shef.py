"""
Asynchronous client for the DirecTV SHEF (Set Top HTTP Exported Functionality) protocol.

Based on the DirecTV SHEF Command Set, DTV-MD-0359 Rev 1.3.C.

SHEF is a plain HTTP/JSON API served on port 8080 of a DirecTV receiver. It has no
authentication: the only gate is the receiver's "External Access" setting
(Menu > Settings > Whole-Home > External Device), and a receiver with it disabled
answers every request with HTTP 403.

The receiver's HTTP server is single-threaded and does not queue requests, so this
client serialises every request behind a lock and spaces consecutive requests apart.
Firing requests concurrently makes the receiver return HTTP 500 and abort commands
part-way through.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Final

import aiohttp
from yarl import URL

_LOG = logging.getLogger(__name__)

DEFAULT_PORT: Final[int] = 8080
DEFAULT_TIMEOUT: Final[float] = 8.0

# Minimum seconds between two consecutive requests to the same receiver. The receiver
# is single-threaded; back-to-back requests make it return HTTP 500. This is the
# smallest spacing that stays responsive for D-pad navigation while remaining stable.
MIN_REQUEST_INTERVAL: Final[float] = 0.3

# A 500 usually means "receiver was still busy", so a serialised retry generally
# succeeds. Retries are spaced more generously than normal requests.
MAX_RETRIES: Final[int] = 2
RETRY_BACKOFF: Final[float] = 1.0

# clientAddr of the host receiver itself. Genie/RVU clients use their MAC address.
HOST_CLIENT_ADDR: Final[str] = "0"

# Live/standby values of /info/mode.
MODE_ACTIVE: Final[int] = 0
MODE_STANDBY: Final[int] = 1

# minor channel value meaning "no minor channel".
NO_MINOR: Final[int] = 65535

# Key press modes accepted by /remote/processKey.
HOLD_PRESS: Final[str] = "keyPress"
HOLD_DOWN: Final[str] = "keyDown"
HOLD_UP: Final[str] = "keyUp"

# The complete, closed set of keys SHEF accepts. Anything else is rejected by the
# receiver, so validate locally rather than burning a request on it.
VALID_KEYS: Final[frozenset[str]] = frozenset(
    {
        "power",
        "poweron",
        "poweroff",
        "format",
        "pause",
        "rew",
        "replay",
        "stop",
        "advance",
        "ffwd",
        "record",
        "play",
        "guide",
        "active",
        "list",
        "exit",
        "back",
        "menu",
        "info",
        "up",
        "down",
        "left",
        "right",
        "select",
        "red",
        "green",
        "yellow",
        "blue",
        "chanup",
        "chandown",
        "prev",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "dash",
        "enter",
    }
)


class SHEFError(Exception):
    """Generic SHEF error."""


class SHEFConnectionError(SHEFError):
    """Receiver unreachable, or the request timed out."""


class SHEFAccessRestricted(SHEFError):
    """Receiver responded with 403: External Access is not enabled."""


class SHEFResponseError(SHEFError):
    """Receiver returned an error response."""

    def __init__(self, message: str, status: int = 0) -> None:
        """Create instance, retaining the HTTP status code."""
        super().__init__(message)
        self.status = status


def parse_channel(channel: str) -> tuple[int, int]:
    """
    Split a channel string into its major and minor parts.

    "202" -> (202, 65535); "202-1" -> (202, 1). A receiver treats minor 65535 as
    "no minor channel", which is what plain numeric channels use.
    """
    text = str(channel).strip()
    for separator in ("-", "."):
        if separator in text:
            major, _, minor = text.partition(separator)
            return int(major), int(minor)
    return int(text), NO_MINOR


def format_channel(major: int, minor: int) -> str:
    """Render a major/minor pair back into a channel string."""
    if minor in (None, NO_MINOR):
        return str(major)
    return f"{major}-{minor}"


@dataclass(frozen=True)
class Version:
    """Response of /info/getVersion."""

    receiver_id: str
    access_card_id: str
    software_version: str
    shef_version: str

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Version":
        """Build from a SHEF response."""
        return Version(
            # receiverId is returned with embedded spaces on some models.
            receiver_id="".join(str(data.get("receiverId", "")).split()),
            access_card_id=str(data.get("accessCardId", "")),
            software_version=str(data.get("stbSoftwareVersion", "")),
            shef_version=str(data.get("version", "")),
        )


@dataclass(frozen=True)
class Location:
    """A tunable endpoint: the host receiver itself, or one of its Genie clients."""

    client_addr: str
    name: str

    @property
    def is_client(self) -> bool:
        """Return True for a Genie/RVU client, False for the host receiver."""
        return self.client_addr != HOST_CLIENT_ADDR

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Location":
        """Build from a SHEF response."""
        return Location(
            client_addr=str(data.get("clientAddr", HOST_CLIENT_ADDR)),
            name=str(data.get("locationName", "Receiver")),
        )


@dataclass(frozen=True)
class Program:
    """A tuned or looked-up program, from /tv/getTuned or /tv/getProgInfo."""

    channel: str
    callsign: str
    title: str
    episode_title: str | None
    rating: str | None
    duration: int
    offset: int
    start_time: int
    program_id: str
    is_recording: bool
    is_off_air: bool
    is_vod: bool
    is_ppv: bool
    # Present only on recordings; None while watching live TV.
    unique_id: str | None
    music_title: str | None
    music_artist: str | None
    music_album: str | None

    @property
    def is_recorded(self) -> bool:
        """Return True when playing back a recording rather than live TV."""
        return self.unique_id is not None

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Program":
        """Build from a SHEF response."""
        music = data.get("music") or {}
        return Program(
            channel=format_channel(
                int(data.get("major", 0)), int(data.get("minor", NO_MINOR))
            ),
            callsign=str(data.get("callsign", "")),
            title=str(data.get("title", "")),
            episode_title=data.get("episodeTitle"),
            rating=data.get("rating"),
            duration=int(data.get("duration", 0) or 0),
            offset=int(data.get("offset", 0) or 0),
            start_time=int(data.get("startTime", 0) or 0),
            program_id=str(data.get("programId", "")),
            is_recording=bool(data.get("isRecording", False)),
            is_off_air=bool(data.get("isOffAir", False)),
            is_vod=bool(data.get("isVod", False)),
            is_ppv=bool(data.get("isPpv", False)),
            unique_id=data.get("uniqueId"),
            music_title=music.get("title"),
            music_artist=music.get("by"),
            music_album=music.get("cd"),
        )


class SHEFClient:
    """
    Client for a single DirecTV receiver.

    All Genie clients are addressed through their host receiver's IP, by passing the
    client's MAC address as ``client_addr`` — you never connect to a client directly.

    Requests are serialised and rate limited across the whole client, which includes
    requests aimed at different Genie clients: they all land on the same
    single-threaded HTTP server.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        request_timeout: float = DEFAULT_TIMEOUT,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Create instance."""
        self.host = host
        self.port = port
        self.request_timeout = request_timeout

        self._session = session
        self._owns_session = session is None
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Return the shared session, creating it on first use."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.request_timeout)
            )
            self._owns_session = True
        return self._session

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """
        Perform a single serialised, rate-limited GET against the receiver.

        Holding the lock across the sleep as well as the request is what guarantees
        the receiver only ever sees one request at a time.
        """
        url = URL.build(scheme="http", host=self.host, port=self.port, path=path)
        query = {k: str(v) for k, v in (params or {}).items() if v is not None}

        async with self._lock:
            last_error: Exception | None = None

            for attempt in range(MAX_RETRIES + 1):
                delay = MIN_REQUEST_INTERVAL if attempt == 0 else RETRY_BACKOFF
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < delay:
                    await asyncio.sleep(delay - elapsed)

                try:
                    return await self._send(url, query)
                except SHEFResponseError as err:
                    # A 500 means the receiver was still busy with the previous
                    # command. Retrying after a pause almost always succeeds.
                    if err.status != 500 or attempt == MAX_RETRIES:
                        raise
                    last_error = err
                    _LOG.debug(
                        "[%s] %s returned 500, retrying (%d/%d)",
                        self.host,
                        path,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                finally:
                    self._last_request_at = time.monotonic()

            raise SHEFResponseError(str(last_error), 500)

    async def _send(self, url: URL, query: dict[str, str]) -> Any:
        """Issue the HTTP request and translate failures into SHEF errors."""
        session = await self._ensure_session()

        try:
            async with session.get(url, params=query) as response:
                # 403 is the receiver telling us External Access is switched off. It
                # is a configuration problem, not a connectivity one, so keep it
                # distinct: the setup flow turns it into an actionable message.
                if response.status == 403:
                    raise SHEFAccessRestricted(
                        "External Access is not enabled on this receiver"
                    )

                body = await response.text()

                if response.status >= 400:
                    raise SHEFResponseError(
                        f"HTTP {response.status} from {url.path}: {body[:200]}",
                        response.status,
                    )

                content_type = response.headers.get("Content-Type", "")
                if "json" not in content_type:
                    raise SHEFResponseError(
                        f"Expected JSON from {url.path}, got {content_type!r}"
                    )

                return await response.json(content_type=None)

        except asyncio.TimeoutError as err:
            raise SHEFConnectionError(
                f"Timeout connecting to receiver at {self.host}"
            ) from err
        except aiohttp.ClientError as err:
            raise SHEFConnectionError(
                f"Error communicating with receiver at {self.host}: {err}"
            ) from err

    # ── Endpoints ────────────────────────────────────────────────────────────

    async def get_version(self) -> Version:
        """Identify the receiver. Works regardless of standby state."""
        return Version.from_dict(await self._request("/info/getVersion"))

    async def get_locations(self) -> list[Location]:
        """
        List the host receiver and every Genie client attached to it.

        Only satellite receivers and RVU clients appear here — someone watching in
        the DirecTV app is not a location.
        """
        data = await self._request("/info/getLocations")
        return [Location.from_dict(item) for item in data.get("locations", [])]

    async def get_options(self) -> list[str]:
        """
        List the command paths this receiver actually advertises.

        Used to detect model variation: the DVR playlist endpoints, for instance, were
        removed in SHEF 1.3 and no longer appear here.
        """
        data = await self._request("/info/getOptions")
        return [
            str(option.get("command", ""))
            for option in data.get("options", [])
            if not option.get("deprecated")
        ]

    async def get_mode(self, client_addr: str = HOST_CLIENT_ADDR) -> int:
        """
        Return MODE_ACTIVE or MODE_STANDBY.

        Every other call must be gated on this: a receiver in standby answers
        /tv/getTuned with an HTTP 500.
        """
        data = await self._request("/info/mode", {"clientAddr": client_addr})
        return int(data.get("mode", MODE_STANDBY))

    async def get_tuned(self, client_addr: str = HOST_CLIENT_ADDR) -> Program:
        """Return what is currently playing. Only valid while the client is active."""
        return Program.from_dict(
            await self._request("/tv/getTuned", {"clientAddr": client_addr})
        )

    async def get_prog_info(
        self, channel: str, client_addr: str = HOST_CLIENT_ADDR
    ) -> Program:
        """
        Look up what is on a channel without tuning to it.

        This is the only guide-adjacent call SHEF offers; there is no lineup endpoint.
        """
        major, minor = parse_channel(channel)
        return Program.from_dict(
            await self._request(
                "/tv/getProgInfo",
                {"major": major, "minor": minor, "clientAddr": client_addr},
            )
        )

    async def tune(self, channel: str, client_addr: str = HOST_CLIENT_ADDR) -> None:
        """
        Change channel.

        The receiver answers as soon as it accepts the request, not once the channel
        has actually changed — there is a 1-2 second lag before /tv/getTuned reflects
        the new channel.
        """
        major, minor = parse_channel(channel)
        await self._request(
            "/tv/tune", {"major": major, "minor": minor, "clientAddr": client_addr}
        )

    async def press_key(
        self,
        key: str,
        client_addr: str = HOST_CLIENT_ADDR,
        hold: str = HOLD_PRESS,
    ) -> None:
        """Emulate a remote key press."""
        key = key.lower()
        if key not in VALID_KEYS:
            raise SHEFError(f"Unsupported remote key: {key}")

        await self._request(
            "/remote/processKey",
            {"key": key, "hold": hold, "clientAddr": client_addr},
        )

    async def close(self) -> None:
        """Close the HTTP session, if this client created it."""
        if self._session and not self._session.closed and self._owns_session:
            await self._session.close()

    async def __aenter__(self) -> "SHEFClient":
        """Async enter."""
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        """Async exit."""
        await self.close()
