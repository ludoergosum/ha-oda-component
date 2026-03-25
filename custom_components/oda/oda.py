"""Oda API client for Home Assistant integration."""

import asyncio
import base64
import datetime
import json
import logging
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

_LOGGER = logging.getLogger(__name__)

NORWEGIAN_TZ = ZoneInfo("Europe/Oslo")

# Norwegian day names -> weekday number (Monday=0)
NORWEGIAN_DAYS = {
    "mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3,
    "fredag": 4, "lørdag": 5, "søndag": 6,
}

# Norwegian month names -> month number
NORWEGIAN_MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8,
    "september": 9, "oktober": 10, "november": 11, "desember": 12,
}

# Relative day words
NORWEGIAN_RELATIVE = {
    "i dag": 0, "idag": 0, "i morgen": 1, "imorgen": 1,
    "i overmorgen": 2, "overmorgen": 2,
}


class CouldNotLogin(Exception):
    def __init__(self, reason=""):
        self.reason = reason
        super().__init__(reason)


class CouldNotFindItemByName(Exception):
    pass


async def _write_oda_token(token: str, oda_token_path: str | Path):
    """Write the Oda session cookies to a file (non-blocking)."""
    def _write():
        os.makedirs(os.path.dirname(oda_token_path), exist_ok=True)
        with open(oda_token_path, "w") as f:
            f.write(token)
    await asyncio.get_event_loop().run_in_executor(None, _write)


async def _read_oda_token(oda_token_path: str | Path) -> str:
    """Read the Oda session cookies from a file (non-blocking)."""
    def _read():
        with open(oda_token_path, "r") as f:
            return f.read().strip()
    return await asyncio.get_event_loop().run_in_executor(None, _read)


def _next_weekday(today: datetime.date, weekday: int) -> datetime.date:
    """Get the next occurrence of a weekday (0=Monday). If today is that day, return today."""
    days_ahead = weekday - today.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return today + datetime.timedelta(days=days_ahead)


def parse_norwegian_datetime(text: str) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Parse Norwegian delivery time strings into start/end datetimes.

    Handles formats like:
    - "Fredag 10:00-14:00"
    - "Torsdag 22. mai 10:00-14:00"
    - "I morgen 14:00-16:00"
    - "22. mai 10:00-14:00"
    """
    if not text:
        return None, None

    text_lower = text.lower().strip()
    today = datetime.date.today()
    target_date = None

    # Try relative days first: "i morgen", "i dag", etc.
    for word, offset in NORWEGIAN_RELATIVE.items():
        if word in text_lower:
            target_date = today + datetime.timedelta(days=offset)
            break

    # Try day name: "fredag", "torsdag", etc.
    if target_date is None:
        for day_name, weekday_num in NORWEGIAN_DAYS.items():
            if day_name in text_lower:
                target_date = _next_weekday(today, weekday_num)
                break

    # Try explicit date: "22. mai" or "22. mai 2025"
    date_match = re.search(r"(\d{1,2})\.\s*(\w+)(?:\s+(\d{4}))?", text_lower)
    if date_match:
        day_num = int(date_match.group(1))
        month_name = date_match.group(2)
        year = int(date_match.group(3)) if date_match.group(3) else today.year
        month_num = NORWEGIAN_MONTHS.get(month_name)
        if month_num:
            target_date = datetime.date(year, month_num, day_num)
            # If the date is in the past, assume next year
            if target_date < today:
                target_date = datetime.date(year + 1, month_num, day_num)

    # Default to today if nothing matched
    if target_date is None:
        target_date = today

    # Extract times: "10:00-14:00" or "kl. 20:00" or just "14:00"
    times = re.findall(r"(\d{1,2}):(\d{2})", text)

    if len(times) >= 2:
        start = datetime.datetime(
            target_date.year, target_date.month, target_date.day,
            int(times[0][0]), int(times[0][1]), tzinfo=NORWEGIAN_TZ,
        )
        end = datetime.datetime(
            target_date.year, target_date.month, target_date.day,
            int(times[1][0]), int(times[1][1]), tzinfo=NORWEGIAN_TZ,
        )
        return start, end
    elif len(times) == 1:
        single = datetime.datetime(
            target_date.year, target_date.month, target_date.day,
            int(times[0][0]), int(times[0][1]), tzinfo=NORWEGIAN_TZ,
        )
        return single, None
    else:
        return None, None


def parse_delivery(response: dict) -> dict | None:
    """Parse an Oda order/delivery response into a structured dict."""
    r = response.get("delivery", {})
    try:
        order_number = r["tracking"]["data"]["order_number"]
    except (KeyError, TypeError):
        return None

    # Parse delivery time window
    delivery_time_text = r.get("delivery_time", "")
    start_time, end_time = parse_norwegian_datetime(delivery_time_text)
    if end_time is None:
        end_time = start_time

    # Parse add-more deadline
    cutoff_text = r.get("cutoff_text")
    add_more_deadline = None
    if cutoff_text:
        deadline, _ = parse_norwegian_datetime(cutoff_text)
        add_more_deadline = deadline

    # Get current status step
    tracking_data = r.get("tracking", {}).get("data", {})
    steps = tracking_data.get("steps", [])
    current_step = tracking_data.get("current_step_number", 1) - 1
    status = steps[current_step]["title"] if 0 <= current_step < len(steps) else "Unknown"

    return {
        "order_id": order_number,
        "order_url": f"https://oda.com/no/account/orders/{order_number}/",
        "status": status,
        "status_text": r.get("status_text", ""),
        "currency": response.get("currency", "NOK"),
        "gross_amount": response.get("gross_amount", 0),
        "address": r.get("delivery_address", ""),
        "doorstep": tracking_data.get("is_doorstep_delivery", False),
        "can_add_more": cutoff_text is not None,
        "add_more_deadline": add_more_deadline,
        "delivery_interval_start": start_time,
        "delivery_interval_end": end_time,
    }


def _clean_items(item_list: list, subkey: str | None = None) -> list[dict]:
    """Normalize product data from various Oda API response formats."""
    out = []
    for item in item_list:
        if subkey:
            item = item.get(subkey, {})
        main_image = None
        images = item.get("images", [])
        if images:
            main_image = images[0].get("thumbnail", {}).get("url")
        product = {
            "id": item.get("id"),
            "name": item.get("name"),
            "full_name": item.get("full_name") or item.get("fullName"),
            "brand": item.get("brand"),
            "brand_id": item.get("brand_id") or item.get("brandId"),
            "name_extra": item.get("name_extra") or item.get("nameExtra"),
            "gross_price": item.get("gross_price") or item.get("grossPrice"),
            "gross_unit_price": item.get("gross_unit_price") or item.get("grossUnitPrice"),
            "unit_price_quantity_abbreviation": (
                item.get("unit_price_quantity_abbreviation")
                or item.get("unitPriceQuantityAbbreviation")
            ),
            "currency": item.get("currency"),
            "front_url": item.get("front_url") or item.get("frontUrl"),
            "image_url": main_image,
            "is_available": (
                item.get("availability", {}).get("is_available")
                or item.get("availability", {}).get("isAvailable")
            ),
        }
        discount = item.get("discount")
        if discount:
            product["discount"] = {
                "is_discounted": discount.get("is_discounted") or discount.get("isDiscounted"),
                "description_short": discount.get("description_short") or discount.get("descriptionShort"),
                "undiscounted_gross_price": (
                    discount.get("undiscounted_gross_price")
                    or discount.get("undiscountedGrossPrice")
                ),
            }
        out.append(product)
    return out


class OdaAPI:
    """Oda API client with CSRF token handling and cookie persistence."""

    base_url = "https://oda.com/api/v1"

    def __init__(
        self,
        username: str,
        password: str,
        oda_token_path: str | Path | None = None,
    ):
        self.session: httpx.AsyncClient | None = None
        self.username = username
        self.password = password
        self.oda_token_path = oda_token_path
        self._orders: dict[str, dict] = {}

    _default_headers = {
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "origin": "https://oda.com",
        "referer": "https://oda.com/",
        "x-client-app": "tienda-web",
        "x-country": "no",
        "x-language": "nb",
        "x-requested-case": "camel",
    }

    async def _ensure_session(self):
        """Create the httpx session lazily, off the event loop to avoid blocking."""
        if self.session is None:
            def _create():
                return httpx.AsyncClient(
                    follow_redirects=True,
                    headers=self._default_headers,
                )
            self.session = await asyncio.get_event_loop().run_in_executor(None, _create)

    def _csrf_headers(self) -> dict:
        """Get CSRF headers from cookie jar for mutating requests."""
        csrf = self.session.cookies.get("csrftoken", domain="oda.com")
        return {"x-csrftoken": csrf} if csrf else {}

    async def _fresh_login(self):
        """Perform a fresh login with username/password."""
        await self._ensure_session()
        if not self.username or not self.password:
            raise CouldNotLogin("Username and password are required")

        # Get CSRF token by loading login page
        await self.session.get("https://oda.com/no/user/login/")

        headers = {
            "content-type": "application/json",
            "referer": "https://oda.com/no/user/login/",
            **self._csrf_headers(),
        }

        resp = await self.session.post(
            url=self.base_url + "/user/login/",
            json={"username": self.username, "password": self.password},
            headers=headers,
        )

        if resp.status_code != 200:
            raise CouldNotLogin(
                f"Could not log in with username '{self.username}'. "
                f"Status: {resp.status_code}, Response: {resp.text}"
            )

        # Extract session cookies (from response + cookie jar)
        session_id = None
        csrf_token = None
        for cookie_name, cookie_value in resp.cookies.items():
            if cookie_name == "sessionid":
                session_id = cookie_value
            elif cookie_name == "csrftoken":
                csrf_token = cookie_value

        # Fall back to cookie jar if login response didn't include them
        if not session_id:
            session_id = self.session.cookies.get("sessionid")
        if not csrf_token:
            csrf_token = self.session.cookies.get("csrftoken")

        if not session_id:
            raise CouldNotLogin("No sessionid cookie received from login")

        _LOGGER.debug("Successfully logged in to Oda")

        if self.oda_token_path:
            cookie_data = {"sessionid": session_id, "csrftoken": csrf_token or ""}
            await _write_oda_token(json.dumps(cookie_data), self.oda_token_path)

    async def login(self):
        """Log in to the Oda API with CSRF handling and cookie persistence."""
        await self._ensure_session()

        # Try to restore session from stored cookies
        if self.oda_token_path:
            try:
                cookie_data_str = await _read_oda_token(self.oda_token_path)
                cookie_data = json.loads(cookie_data_str)
                if "sessionid" in cookie_data:
                    self.session.cookies.set("sessionid", cookie_data["sessionid"], domain="oda.com")
                if "csrftoken" in cookie_data:
                    self.session.cookies.set("csrftoken", cookie_data["csrftoken"], domain="oda.com")
                # Verify the session is still valid
                try:
                    await self.get_user_info()
                    _LOGGER.debug("Restored valid Oda session from file")
                    return
                except httpx.HTTPStatusError:
                    _LOGGER.debug("Stored session expired, performing fresh login")
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                _LOGGER.debug("No valid stored session, performing fresh login")

        await self._fresh_login()

    async def get_user_info(self):
        """Retrieve user information (used to verify session is valid)."""
        await self._ensure_session()
        response = await self.session.get(self.base_url + "/user/refresh/")
        response.raise_for_status()
        return response.json()

    async def check_login_or_retry_login(self):
        """Verify session is valid, re-login if expired."""
        await self._ensure_session()
        try:
            await self.get_user_info()
        except httpx.HTTPStatusError:
            _LOGGER.debug("Session expired, performing fresh login")
            if self.oda_token_path:
                try:
                    os.remove(self.oda_token_path)
                except OSError:
                    pass
            await self._fresh_login()

    # ── Search ──────────────────────────────────────────────

    async def search(self, search_term: str):
        """Search for products by name (raw results)."""
        await self.check_login_or_retry_login()
        params = {"q": search_term, "type": "product", "size": 50}
        response = await self.session.get(
            self.base_url + "/search/mixed/", params=params,
        )
        response.raise_for_status()
        return response.json().get("items", [])

    async def search_products(self, search_term: str) -> list[dict]:
        """Search for products with cleaned data and favorites flag."""
        await self.check_login_or_retry_login()
        response = await self.session.get(
            self.base_url + "/search/mixed/",
            params={"q": search_term, "type": "product", "size": 50},
        )
        response.raise_for_status()
        results = _clean_items(response.json()["items"], subkey="attributes")

        favorites = await self.get_favorite_products()
        fav_ids = {p["id"] for p in favorites} if favorites else set()
        for product in results:
            product["is_favorite"] = product["id"] in fav_ids
        return results

    async def search_recipes(self, search_term: str) -> list[dict]:
        """Search for recipes by name."""
        await self.check_login_or_retry_login()
        response = await self.session.get(
            self.base_url + "/search/mixed/",
            params={"q": search_term, "type": "recipe", "size": 50},
        )
        response.raise_for_status()
        return _clean_items(response.json()["items"], subkey="attributes")

    async def get_top_previously_bought_from_search(self, search_term: str):
        """Get the top search result for a search term.

        Returns the attributes dict of the first product result.
        """
        search_results = await self.search(search_term)
        for item in search_results:
            if item.get("type") == "product":
                attrs = item.get("attributes", {})
                if attrs.get("id"):
                    _LOGGER.debug(
                        "Found product for '%s': %s (id=%s)",
                        search_term, attrs.get("fullName", "unknown"), attrs.get("id"),
                    )
                    return attrs
        _LOGGER.warning("No products found for search term '%s'", search_term)
        return None

    # ── Products ────────────────────────────────────────────

    async def get_favorite_products(self) -> list[dict]:
        """Get the user's most purchased products."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + "/app-components/most-purchased/")
        response.raise_for_status()
        for block in response.json().get("blocks", []):
            if block["id"] == "most-purchased-products":
                return _clean_items(block["products"])
        return []

    async def get_promotions(self) -> list[dict]:
        """Get current promotions/discounts."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + "/app-components/home/")
        response.raise_for_status()
        for block in response.json().get("blocks", []):
            if block["id"] == "discounts-feed-block":
                return _clean_items(block["products"])
        return []

    async def get_product_image(self, product_id: int) -> dict:
        """Fetch a product's image and return it as a base64-encoded string."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + f"/products/{product_id}/")
        response.raise_for_status()
        product = response.json()

        image_url = None
        images = product.get("images", [])
        if images:
            image_url = images[0].get("thumbnail", {}).get("url")

        if not image_url:
            return {"product_id": product_id, "image_base64": None, "content_type": None}

        img_response = await self.session.get(image_url)
        img_response.raise_for_status()

        content_type = img_response.headers.get("content-type", "image/jpeg")
        image_base64 = base64.b64encode(img_response.content).decode("utf-8")

        return {
            "product_id": product_id,
            "image_base64": image_base64,
            "content_type": content_type,
        }

    # ── Product lists ───────────────────────────────────────

    async def get_product_lists(self) -> list[dict]:
        """Get all product lists for the user."""
        await self.check_login_or_retry_login()
        response = await self.session.get(
            self.base_url + "/product-lists/",
            params={"page": 1, "sort": "default", "filter": "product_lists", "size": 50},
        )
        response.raise_for_status()
        return [
            {
                "id": lst["id"],
                "title": lst["title"],
                "description": lst.get("description", ""),
                "number_of_products": lst.get("numberOfProducts", 0),
                "last_bought_date": lst.get("lastBoughtDate"),
            }
            for lst in response.json().get("results", [])
        ]

    async def get_product_list_items(self, list_id: int) -> dict:
        """Get items in a specific product list."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + f"/product-lists/{list_id}/")
        response.raise_for_status()
        data = response.json()
        items = []
        for entry in data.get("items", []):
            product = entry.get("product", {})
            image_url = None
            images = product.get("images", [])
            if images:
                image_url = images[0].get("thumbnail", {}).get("url")
            items.append({
                "id": product.get("id"),
                "name": product.get("name"),
                "full_name": product.get("fullName"),
                "brand": product.get("brand"),
                "gross_price": product.get("grossPrice"),
                "gross_unit_price": product.get("grossUnitPrice"),
                "unit_price_quantity_abbreviation": product.get("unitPriceQuantityAbbreviation"),
                "currency": product.get("currency"),
                "is_available": product.get("availability", {}).get("isAvailable"),
                "image_url": image_url,
                "quantity": entry.get("quantity", 1),
            })
        return {
            "id": data["id"],
            "title": data["title"],
            "number_of_products": data.get("number_of_products", len(items)),
            "items": items,
        }

    # ── Cart ────────────────────────────────────────────────

    async def get_cart(self):
        """Retrieve the full cart."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + "/cart/")
        response.raise_for_status()
        return response.json()

    async def get_cart_items(self):
        """Retrieve items in the cart (from groups[].items[])."""
        cart = await self.get_cart()
        if not isinstance(cart, dict):
            return []
        items = []
        for group in cart.get("groups", []):
            items.extend(group.get("items", []))
        return items

    async def add_to_cart_by_id(self, item_id: int, quantity: int = 1):
        """Add an item to the cart by product ID."""
        await self.check_login_or_retry_login()
        response = await self.session.post(
            self.base_url + "/cart/items/?group_by=recipes",
            json={"items": [{"product_id": item_id, "quantity": quantity}]},
            headers=self._csrf_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def add_to_cart_by_name(self, item_name: str):
        """Search for an item by name and add the best match to cart.

        If item_name starts with 'id:', treat the rest as a product ID
        and add directly without searching.
        """
        if item_name.startswith("id:"):
            product_id = int(item_name[3:].strip())
            return await self.add_to_cart_by_id(product_id)
        await self.check_login_or_retry_login()
        top = await self.get_top_previously_bought_from_search(item_name)
        if not top:
            raise CouldNotFindItemByName(f"Could not find item by name '{item_name}'")
        item_id = top["id"]
        return await self.add_to_cart_by_id(item_id)

    async def remove_from_cart_by_id(self, item_id: int):
        """Remove an item from the cart by setting quantity to -1."""
        return await self.add_to_cart_by_id(item_id, quantity=-1)

    # ── Orders / Deliveries ─────────────────────────────────

    async def get_deliveries(self):
        """Retrieve and parse deliveries (orders)."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + "/orders")
        response.raise_for_status()
        orders_response = response.json()

        all_orders = [
            o
            for period in orders_response.get("results", [])
            for o in period.get("orders", [])
        ]

        if not self._orders:
            for o in all_orders:
                parsed = parse_delivery(o)
                if parsed:
                    self._orders[parsed["order_id"]] = parsed
        else:
            for o in all_orders:
                try:
                    order_id = o["delivery"]["tracking"]["data"]["order_number"]
                except (KeyError, TypeError):
                    continue
                if order_id not in self._orders or self._orders[order_id]["status_text"] != o["delivery"].get("status_text"):
                    if order_id in self._orders:
                        self._orders.pop(order_id)
                    parsed = parse_delivery(o)
                    if parsed:
                        self._orders[parsed["order_id"]] = parsed

        return list(self._orders.values())

    async def get_order(self, order_id: str) -> list[dict]:
        """Get detailed items for a specific order."""
        await self.check_login_or_retry_login()
        response = await self.session.get(self.base_url + f"/orders/{order_id}")
        response.raise_for_status()
        return response.json()["items"]

    def get_order_items(self, order_data: dict) -> list[dict]:
        """Flatten an order's itemGroups into a clean list of products."""
        groups = order_data.get("itemGroups") or order_data.get("item_groups", [])
        items = []
        for group in groups:
            if group.get("type") != "category":
                continue
            for item in group.get("items", []):
                quantity = item.get("quantity", 1)
                gross_amount = item.get("grossAmount") or item.get("gross_amount", 0)
                unit_price = gross_amount / quantity if quantity > 0 else gross_amount
                items.append({
                    "id": item.get("productId") or item.get("product_id"),
                    "name": item.get("description", "Unknown"),
                    "gross_price": unit_price,
                    "total_price": gross_amount,
                    "currency": item.get("currency", "NOK"),
                    "quantity": quantity,
                    "category": group.get("name", "Unknown"),
                    "has_discount": bool(item.get("discount")),
                    "image_url": item.get("productImage"),
                })
        return items
