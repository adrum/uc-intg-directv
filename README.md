# DirecTV integration for Unfolded Circle Remote Two/3

Control DirecTV receivers and their Genie clients from an Unfolded Circle remote, over
the receiver's built-in SHEF network API.

Each receiver and each Genie client you select during setup gets its own media player,
remote, channel select, and now-playing sensors, so every TV in the house can be driven
independently.

## Requirements

- A DirecTV receiver, model **H21/HR20 or newer**. Older models do not speak SHEF.
- **Whole-Home DVR service** on the account. The setting that enables network control
  lives under the Whole-Home menu, and is not present without it.
- The receiver on a **static IP address**. DirecTV receivers have no discovery
  mechanism, so the integration is configured with a fixed address.

## Enabling network control

On each receiver, go to:

**Menu → Settings → Whole-Home → External Device → External Access = Allow**

and accept the caution screen. Menu wording varies slightly across software versions.

Until this is enabled the receiver answers every request with HTTP 403, and setup will
report an authorization error.

Connect the receiver by **either** Ethernet or coax/DECA — never both at once.

## Installation

1. Download the integration package (`.tar.gz`) from the [Releases page][releases].
2. Upload it via the Remote's web configurator: **Integrations → Install custom**.
3. Configure your receiver through the setup wizard.

## Updating

1. Download the new integration package (`.tar.gz`) from the [Releases page][releases].
2. Remove the existing integration from the Remote (twice). Your configured activity
   settings are preserved.
3. Upload the new archive via the web configurator: **Integrations → Install custom**.
4. Configure your receiver through the setup wizard.

[releases]: https://github.com/adrum/uc-intg-directv/releases

## Setup

1. Add the integration and enter the receiver's IP address.
2. Optionally enter favorite channels, comma separated (`202, 206, 231-1`).
3. The integration asks the receiver which locations it serves — itself and any Genie
   clients — and you pick the ones to control.

Favorite channels become the media player's source list and the channel select entity.
Channel names are looked up from the receiver, so `202, 206` becomes `202 ESPN`,
`206 ESPN2` automatically.

## Entities

Per selected location:

| Entity | Provides |
| --- | --- |
| Media player | Power, transport, D-pad, numpad, guide/info/menu, channel up/down, favorite channels as sources, now-playing title and progress |
| Remote | Every DirecTV key as a simple command, physical button mappings, and three UI pages (navigation, playback, numpad) |
| Select | Jump to a favorite channel |
| Sensors | Channel, channel name, title, episode title, rating, recording |

## What DirecTV does not allow

These are limits of the SHEF protocol itself, not of this integration:

- **No volume, mute, or audio control of any kind.** The receiver has no audio commands.
  Volume belongs to the TV or AV receiver downstream of it.
- **No channel lineup or guide.** There is no endpoint to list channels, which is why
  favorite channels are entered by hand.
- **No artwork.** No response carries a poster or thumbnail.
- **No playback state.** Play, pause, fast-forward and rewind can be *sent*, but the
  receiver will not report which one is active. The media player's play/pause button
  therefore assumes the state and resets its assumption on a channel change; discrete
  **Play** and **Pause** commands are available when that assumption drifts.
- **No DVR playlist.** The playlist endpoints were removed by DirecTV in 2011.
- **No recording scheduling, search, or deep links.**

## Behaviour worth knowing

The receiver's HTTP server is **single-threaded and does not queue requests** — sending
commands concurrently makes it fail them part-way through. The integration serialises
every request and spaces them apart, which is why a burst of rapid key presses is paced
rather than fired at once. `MIN_REQUEST_INTERVAL` in `intg-directv/shef.py` controls the
spacing if you want to trade responsiveness against stability.

A receiver in standby answers most queries with an error, so state is polled from
`/info/mode` first. The poll interval is configurable at setup (default 30s); the
receiver is slow, so short intervals are not recommended.

Genie 2 (HS17) is **untested** — it is headless, and no source confirms whether it
responds to SHEF on its own address. Target its clients instead.

## Running outside the Remote

The driver can also run on any machine on the same network instead of being uploaded to
the Remote, which discovers it over mDNS. This is the easier way to develop against a
real receiver, since you get logs and a fast reload loop:

```bash
pip install -r requirements.txt
UC_LOG_LEVEL=DEBUG python intg-directv/driver.py
```

Docker images are published to `ghcr.io/adrum/uc-intg-directv`.

## Releasing

Pushing a `v*` tag builds the aarch64 driver binary, packages it, and publishes a
GitHub release with the `.tar.gz` attached:

```bash
git tag v0.1.0
git push origin main --tags
```

## Credits

The SHEF client is based on [ctalkington/python-directv][pd], rewritten against the
[DirecTV SHEF Command Set v1.3.C][spec]. Project structure follows
[JackJPowell/uc-intg-yamaha-avr][yamaha] and the [`ucapi-framework`][fw] base classes.

[pd]: https://github.com/ctalkington/python-directv
[spec]: https://blog.solidsignal.com/docs/DTV-MD-0359-DIRECTV_SHEF_Command_Set-V1.3.C.pdf
[yamaha]: https://github.com/JackJPowell/uc-intg-yamaha-avr
[fw]: https://pypi.org/project/ucapi-framework/

## License

Mozilla Public License 2.0 — see [LICENSE](LICENSE).
