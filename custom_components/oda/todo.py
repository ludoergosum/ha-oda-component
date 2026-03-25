"""A todo platform for Oda."""

import asyncio
import logging

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OdaDataUpdateCoordinator
from .oda import CouldNotFindItemByName

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the OurGroceries todo platform config entry."""
    coordinator: OdaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OdaTodoListEntity(coordinator)])


class OdaTodoListEntity(
    CoordinatorEntity[OdaDataUpdateCoordinator], TodoListEntity
):
    """An Oda TodoListEntity."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(
        self,
        coordinator: OdaDataUpdateCoordinator,
    ) -> None:
        """Initialize OdaTodoListEntity."""
        super().__init__(coordinator=coordinator)
        self._attr_unique_id = f"oda_{coordinator.oda.username}"
        self._attr_name = "Oda"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data is None:
            self._attr_todo_items = None
        else:
            self._attr_todo_items = [
                TodoItem(
                    summary=item['product']['fullName'],
                    uid=str(item['product']['id']),
                    status=TodoItemStatus.NEEDS_ACTION,
                )
                for item in self.coordinator.data['cart']
            ]
        super()._handle_coordinator_update()

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a To-do item.

        Raises CouldNotFindItemByName if the item can't be found on Oda,
        so the calling automation knows the add failed and can skip removal.
        """
        if item.status != TodoItemStatus.NEEDS_ACTION:
            raise ValueError("Only active tasks may be created.")
        await self.coordinator.oda.add_to_cart_by_name(item.summary)
        await self.coordinator.async_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete a To-do item."""
        await asyncio.gather(
            *[
                self.coordinator.oda.remove_from_cart_by_id(int(uid))
                for uid in uids
            ]
        )
        await self.coordinator.async_refresh()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass update state from existing coordinator data."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()
