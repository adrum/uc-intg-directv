#!/usr/bin/env python3
"""
Remote Two/3 integration driver for DirecTV receivers.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os

from const import SENSORS, DirecTVConfig
from media_player import DirecTVMediaPlayer
from receiver import DirecTVReceiver
from remote import DirecTVRemote
from select_entity import DirecTVChannelSelect
from sensor import DirecTVSensor
from setup import DirecTVSetupFlow
from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path


async def main() -> None:
    """Start the integration driver."""
    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "INFO").upper()
    for module in ("driver", "receiver", "setup", "shef", "media_player", "remote"):
        logging.getLogger(module).setLevel(level)

    # Every location the user selected gets a full set of entities. The factories run
    # once per configured receiver, with that receiver's device instance.
    driver = BaseIntegrationDriver(
        device_class=DirecTVReceiver,
        entity_classes=[
            lambda cfg, dev: [
                DirecTVMediaPlayer(cfg, dev, location) for location in dev.locations
            ],
            lambda cfg, dev: [
                DirecTVRemote(cfg, dev, location) for location in dev.locations
            ],
            lambda cfg, dev: [
                DirecTVChannelSelect(cfg, dev, location) for location in dev.locations
            ],
            lambda cfg, dev: [
                DirecTVSensor(cfg, dev, location, sensor)
                for location in dev.locations
                for sensor in SENSORS
            ],
        ],
    )

    driver.config_manager = BaseConfigManager(
        get_config_path(driver.api.config_dir_path),
        driver.on_device_added,
        driver.on_device_removed,
        config_class=DirecTVConfig,
    )

    await driver.register_all_device_instances()

    # DirecTV receivers have no discovery mechanism, so setup is manual entry only.
    setup_handler = DirecTVSetupFlow.create_handler(driver)
    await driver.api.init("driver.json", setup_handler)

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
