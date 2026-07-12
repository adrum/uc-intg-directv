"""
Sensor entities exposing the now-playing program at a DirecTV location.

:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging

from const import DirecTVConfig, LocationConfig, SensorConfig
from receiver import DirecTVReceiver
from ucapi import EntityTypes
from ucapi.sensor import Attributes, DeviceClasses, States
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SensorEntity

_LOG = logging.getLogger(__name__)


class DirecTVSensor(SensorEntity):
    """A single now-playing sensor for one DirecTV location."""

    def __init__(
        self,
        config_device: DirecTVConfig,
        device: DirecTVReceiver,
        location: LocationConfig,
        sensor_config: SensorConfig,
    ) -> None:
        """Initialize the entity."""
        self._device = device
        self._client_addr = location.client_addr
        self._sensor_id = sensor_config.identifier

        entity_id = create_entity_id(
            EntityTypes.SENSOR,
            config_device.identifier,
            f"{location.client_addr}_{sensor_config.identifier}",
        )
        _LOG.debug("Initializing sensor entity: %s", entity_id)

        super().__init__(
            identifier=entity_id,
            name=f"{location.name} {sensor_config.name}",
            features=[],
            attributes={
                Attributes.STATE: States.UNKNOWN,
                Attributes.VALUE: sensor_config.default,
            },
            device_class=DeviceClasses.CUSTOM,
        )

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync entity state from the device after a poll or reconnect."""
        if self._device is None:
            self.set_unavailable()
            return

        attributes = self._device.get_sensor_attributes(
            self._client_addr, self._sensor_id
        )
        if attributes is not None:
            self.update(attributes)
