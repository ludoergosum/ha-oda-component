# Oda Custom Component for Home Assistant

A Home Assistant custom component for [Oda](https://oda.com) — Norway's online grocery store.

## Features

- **Todo list entity** — Your Oda cart as a HA todo list. Add items by name or product ID (`id:1234`), remove items.
- **Calendar entity** — Upcoming deliveries shown as calendar events, including delivery windows and "add more" deadlines.
- **Full API client** — Search products, browse favorites, promotions, product lists, fetch product images, and view order history.

## Installation

### HACS (Manual)

1. In HACS, go to **Integrations** → **⋮** → **Custom repositories**
2. Add this repository URL and select **Integration** as the category
3. Install the integration and restart Home Assistant

### Manual

1. Copy the `custom_components/oda` folder into your Home Assistant `custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Oda**
3. Enter your Oda email and password

## Entities

### Todo: Oda Cart

Your Oda shopping cart exposed as a todo list.

- **Add items** by name (searches Oda and picks the top result) or by product ID using the `id:` prefix (e.g. `id:1132` for milk)
- **Remove items** from your cart

### Calendar: Oda Deliveries

Shows upcoming deliveries as calendar events with:

- Delivery time window (start/end)
- Order status and tracking
- Doorstep delivery info
- "Add more" deadline events

## API Methods Available

The `OdaAPI` class exposes these methods for use in automations or other integrations:

| Method | Description |
|---|---|
| `search_products(term)` | Search products with favorites flag |
| `search_recipes(term)` | Search recipes |
| `get_favorite_products()` | Most purchased products |
| `get_promotions()` | Current discounts |
| `get_product_lists()` | Saved product lists |
| `get_product_list_items(id)` | Items in a product list |
| `get_product_image(id)` | Product image as base64 |
| `get_cart()` / `get_cart_items()` | Current cart |
| `add_to_cart_by_name(name)` | Search and add to cart |
| `add_to_cart_by_id(id, qty)` | Add by product ID |
| `remove_from_cart_by_id(id)` | Remove from cart |
| `get_deliveries()` | Parsed delivery list |
| `get_order(id)` | Detailed order items |

## Requirements

- Home Assistant 2024.1+
- An [Oda](https://oda.com) account (Norwegian grocery delivery)
- `httpx` (installed automatically)

## License

MIT
