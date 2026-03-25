"""A calendar platform for Oda."""

import datetime

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OdaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Oda Calendar platform config entry."""
    coordinator: OdaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OdaCalendarEntity(coordinator)])


class OdaCalendarEntity(
    CoordinatorEntity[OdaDataUpdateCoordinator], CalendarEntity
):
    """An Oda CalendarEntity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OdaDataUpdateCoordinator,
    ) -> None:
        """Initialize OdaCalendarEntity."""
        super().__init__(coordinator=coordinator)
        self._attr_unique_id = f"oda_calendar_{coordinator.oda.username}"
        self._attr_name = "Oda Deliveries"
        self._events: list[CalendarEvent] = []
        self._event: CalendarEvent | None = None

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        return self._event

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> list[CalendarEvent]:
        """Get all events in a specific time frame."""
        return [
            event for event in self._events
            if event.end_datetime_local > start_date
            and event.start_datetime_local < end_date
        ]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.data is None:
            self._events = []
        else:
            _events = []
            deliveries = self.coordinator.data.get("deliveries", [])
            # Filter out deliveries with no parsed dates
            valid = [d for d in deliveries if d.get("delivery_interval_start")]
            for item in sorted(valid, key=lambda x: x["delivery_interval_start"]):
                if item["can_add_more"]:
                    can_add_more_text = f"Can add more. Deadline: {item['add_more_deadline']}"
                else:
                    can_add_more_text = "Cannot add more"
                doorstep_text = "Doorstep delivery" if item["doorstep"] else "Not doorstep delivery"

                _events.append(
                    CalendarEvent(
                        start=item["delivery_interval_start"],
                        end=item["delivery_interval_end"],
                        summary=f"Oda order {item['order_id']} - {item['status']}",
                        description=(
                            f"{item['status_text']} - {item['order_id']}\n"
                            f"{doorstep_text}\n"
                            f"{can_add_more_text}\n"
                            f"{item['gross_amount']} {item['currency']}"
                        ),
                        location=item["address"],
                        uid=item["order_id"],
                    )
                )
                if item["can_add_more"] and item.get("add_more_deadline"):
                    _events.append(
                        CalendarEvent(
                            start=item["add_more_deadline"],
                            end=item["add_more_deadline"],
                            summary=f"Oda add more deadline {item['order_id']}",
                            description=f"Add more deadline for order {item['order_id']}",
                            location=item["address"],
                            uid=f"{item['order_id']}_add_more",
                        )
                    )
            self._events = _events
        self._event = self._events[-1] if self._events else None
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass update state from existing coordinator data."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()
