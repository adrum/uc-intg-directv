# Changelog

## v0.1.1

- Fixed activities failing with a timeout when turning the receiver on. A receiver that is powered on but not showing live TV (a menu, the guide, the home screen) answers the "what's tuned" query with a 500; that was being retried, holding the connection for ~3s each poll and blocking commands like power on until they timed out. The query now fails fast and leaves the connection free.

## v0.1.0

- Initial release: media player, remote, sensor, and favorite-channel select entities for DirecTV receivers and Genie clients over SHEF.
