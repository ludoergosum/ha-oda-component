"""The Oda coordinator."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .oda import OdaAPI
from .const import DOMAIN

SCAN_INTERVAL = 60

_LOGGER = logging.getLogger(__name__)


class OdaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Class to manage fetching Oda data."""

    def __init__(self, hass: HomeAssistant, oda: OdaAPI) -> None:
        """Initialize global Oda data updater."""
        self.oda = oda
        self._cached_cart: list[dict] = []
        self._cached_deliveries: list[dict] = []
        interval = timedelta(seconds=SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )

    async def _update_data(self) -> None:
        self._cached_cart = await self.oda.get_cart_items()
        self._cached_deliveries = await self.oda.get_deliveries()

    async def _async_update_data(self) -> dict[str, dict]:
        """Fetch data from Oda."""
        await self._update_data()

        return {"cart": self._cached_cart, "deliveries": self._cached_deliveries}
