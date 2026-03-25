"""The Oda integration."""

from __future__ import annotations

from aiohttp import ClientError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import OdaDataUpdateCoordinator
from .oda import OdaAPI, CouldNotLogin

PLATFORMS: list[Platform] = [Platform.TODO, Platform.CALENDAR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Oda from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    data = entry.data
    token_path = hass.config.path(".oda_token")
    oda = OdaAPI(
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        oda_token_path=token_path,
    )
    try:
        await oda.login()
    except (TimeoutError, ClientError) as error:
        raise ConfigEntryNotReady from error
    except CouldNotLogin:
        return False

    coordinator = OdaDataUpdateCoordinator(hass, oda)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
