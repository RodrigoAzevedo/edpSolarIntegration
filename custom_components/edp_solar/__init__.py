from .const import DOMAIN
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_registry import async_migrate_entries
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .edp_solar_api import EdpSolarApi

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up configured EDP Solar."""
    # We allow setup only through config flow type of config
    return True

# Minimal file, can be empty for now
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EDP Solar from a config entry."""
    _LOGGER.debug("Starting EDP Solar Integration")
    session = async_get_clientsession(hass)    
    # Create and start the API
    api = EdpSolarApi(entry.data["username"], entry.data["password"], hass)
    api.start()
    # Store config entry data if needed
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] =  api
    # Forward setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True
