from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfPower, UnitOfEnergy
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up EDP Solar sensors from a config entry."""
    # Retrieve the API instance
    api = hass.data[DOMAIN][entry.entry_id]

    # Create sensors with entry_id
    sensors = [
        EdpSolarInstantPowerProducedSensor(api, entry.entry_id),
        EdpSolarInstantPowerConsumedSensor(api, entry.entry_id),
        EdpSolarInstantPowerFromGridSensor(api, entry.entry_id),
        EdpSolarInstantPowerInjectedSensor(api, entry.entry_id),
        EdpSolarAvailableDeviceIdsSensor(api, entry.entry_id),
        EdpSolarHouseIdSensor(api, entry.entry_id),
        EdpSolarUserIdSensor(api, entry.entry_id),
        EdpSolarEnergyConsumed(api, entry.entry_id),
        EdpSolarEnergyFromGrid(api, entry.entry_id),
        EdpSolarEnergyInjected(api, entry.entry_id),
        EdpSolarEnergyProduced(api, entry.entry_id),
    ]
    async_add_entities(sensors)

    # Listen for updates from the API (triggered by MQTT callback)
    async def _update_sensors():
        for sensor in sensors:
            sensor.async_schedule_update_ha_state(True)

    entry.async_on_unload(
        async_dispatcher_connect(hass, "edp_solar_update", _update_sensors)
    )

class EdpSolarBaseSensor(SensorEntity):
    """Base sensor for EDP Solar."""

    def __init__(self, api, entry_id):
        self.api = api
        self._entry_id = entry_id

    @property
    def should_poll(self):
        return False  # Data is pushed via MQTT

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": "EDP Solar",
            "manufacturer": "EDP",
            "model": "EDP Solar Integration",
        }

class EdpSolarInstantPowerProducedSensor(EdpSolarBaseSensor):
    _attr_name = "Instant Power Produced"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:solar-power"
    _attr_unique_id = "edp_solar_instant_power_produced"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        return self.api.get_values().get("instant_power_produced")

class EdpSolarInstantPowerConsumedSensor(EdpSolarBaseSensor):
    _attr_name = "Instant Power Consumed"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:flash"
    _attr_unique_id = "edp_solar_instant_power_consumed"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        return self.api.get_values().get("instant_power_consumed")

class EdpSolarInstantPowerInjectedSensor(EdpSolarBaseSensor):
    _attr_name = "Instant Power Injected"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:transmission-tower-export"
    _attr_unique_id = "edp_solar_instant_power_injected"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        return self.api.get_values().get("instant_power_injected")

class EdpSolarInstantPowerFromGridSensor(EdpSolarBaseSensor):
    _attr_name = "Instant Power From Grid"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:transmission-tower"
    _attr_unique_id = "edp_solar_instant_power_from_grid"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        return self.api.get_values().get("instant_power_from_grid")

class EdpSolarAvailableDeviceIdsSensor(EdpSolarBaseSensor):
    _attr_name = "Available Device Ids"
    _attr_icon = "mdi:devices"
    _attr_unique_id = "edp_solar_available_device_ids"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        # Return as a comma-separated string for display
        ids = self.api.get_values().get("available_device_ids")
        return ", ".join(ids) if ids else None

class EdpSolarHouseIdSensor(EdpSolarBaseSensor):
    _attr_name = "House Id"
    _attr_icon = "mdi:home"
    _attr_unique_id = "edp_solar_house_id"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        return self.api.get_values().get("house_id")

class EdpSolarUserIdSensor(EdpSolarBaseSensor):
    _attr_name = "User Id"
    _attr_icon = "mdi:account"
    _attr_unique_id = "edp_solar_user_id"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        return self.api.get_values().get("user_id")

class EdpSolarEnergyConsumed(EdpSolarBaseSensor):
    _attr_name = "Energy Consumed"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_icon = "mdi:flash"
    _attr_unique_id = "edp_solar_energy_consumed"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        ws = self.api.get_values().get("energy_consumed")
        return ws / 3600 if ws is not None else None  # Convert to Wh if desired

class EdpSolarEnergyFromGrid(EdpSolarBaseSensor):
    _attr_name = "Energy From Grid"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_icon = "mdi:transmission-tower"
    _attr_unique_id = "edp_solar_energy_from_grid"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        ws = self.api.get_values().get("energy_from_grid")
        return ws / 3600 if ws is not None else None  # Convert to Wh if desired

class EdpSolarEnergyInjected(EdpSolarBaseSensor):
    _attr_name = "Energy Injected"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_icon = "mdi:transmission-tower-export"
    _attr_unique_id = "edp_solar_energy_injected"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        ws = self.api.get_values().get("energy_injected")
        return ws / 3600 if ws is not None else None  # Convert to Wh if desired

class EdpSolarEnergyProduced(EdpSolarBaseSensor):
    _attr_name = "Energy Produced"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_icon = "mdi:solar-power"
    _attr_unique_id = "edp_solar_energy_produced"

    def __init__(self, api, entry_id):
        super().__init__(api, entry_id)

    @property
    def native_value(self):
        ws = self.api.get_values().get("energy_produced")
        return ws / 3600 if ws is not None else None  # Convert to Wh if desired
