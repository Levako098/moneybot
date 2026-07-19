from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.services.automation import render_template


logger = logging.getLogger("moneybot.auto_delivery")
AUTO_DELIVERY_PATH = Path(__file__).resolve().parent.parent / "data" / "auto_delivery.json"

DELIVERY_VARIABLES = (
    "username",
    "order_id",
    "lot",
    "sum",
    "amount",
    "account",
)


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    order_id: str
    lot_id: str | None = None
    buyer_username: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class RaiseLotsResult:
    status: str
    total_lots: int = 0
    categories_total: int = 0
    categories_raised: int = 0
    raised_categories: tuple[str, ...] = ()
    errors: tuple[tuple[str, str], ...] = ()


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _raise_error_reason(error: Exception) -> str:
    reason = str(getattr(error, "error_message", None) or "").strip()
    response = getattr(error, "response", None)
    if not reason and response is not None:
        try:
            payload = response.json()
        except (TypeError, ValueError):
            payload = {}
        if isinstance(payload, dict):
            reason = str(
                payload.get("msg")
                or payload.get("MSG")
                or payload.get("error")
                or ""
            ).strip()
    if not reason:
        wait_time = getattr(error, "wait_time", None)
        if wait_time:
            reason = f"FunPay просит подождать {wait_time} сек."
    return reason[:300] if reason else type(error).__name__


class AutoDeliveryService:
    def __init__(self, account: Any, path: Path = AUTO_DELIVERY_PATH) -> None:
        self.account = account
        self.path = path
        self._lock = threading.RLock()
        self._raise_lock = threading.Lock()
        self._in_progress: set[str] = set()
        self._lots_cache: list[Any] = []
        self._lots_cached_at = 0.0
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8-sig") as data_file:
                raw = json.load(data_file)
        except FileNotFoundError:
            raw = {}
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать настройки автовыдачи")
            raw = {}
        lots = raw.get("lots") if isinstance(raw, dict) else {}
        delivered = raw.get("delivered_orders") if isinstance(raw, dict) else []
        data = {
            "lots": lots if isinstance(lots, dict) else {},
            "delivered_orders": delivered if isinstance(delivered, list) else [],
        }
        self._save(data)
        return data

    def _save(self, data: dict[str, Any] | None = None) -> None:
        current = data if data is not None else self._data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as data_file:
            json.dump(current, data_file, ensure_ascii=False, indent=2)
            data_file.write("\n")
        temporary.replace(self.path)

    def get_rules(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._data["lots"])

    def get_rule(self, lot_id: int | str) -> dict[str, Any] | None:
        with self._lock:
            rule = self._data["lots"].get(str(lot_id))
            return copy.deepcopy(rule) if rule else None

    def get_lots(self, refresh: bool = False) -> list[Any]:
        with self._lock:
            cache_valid = self._lots_cache and time.monotonic() - self._lots_cached_at < 60
            if not refresh and cache_valid:
                return list(self._lots_cache)
        if self.account is None:
            return []
        lots = self.account.get_user(self.account.id).get_lots()
        lots = sorted(lots, key=lambda lot: (str(lot.title or "").casefold(), str(lot.id)))
        with self._lock:
            self._lots_cache = list(lots)
            self._lots_cached_at = time.monotonic()
        return lots

    def raise_all_lots(self) -> RaiseLotsResult:
        if not self._raise_lock.acquire(blocking=False):
            return RaiseLotsResult("busy")
        try:
            lots = self.get_lots(refresh=True)
            categories: dict[int, dict[str, Any]] = {}
            for lot in lots:
                subcategory = getattr(lot, "subcategory", None)
                category = getattr(subcategory, "category", None)
                category_id = getattr(category, "id", None)
                subcategory_id = getattr(subcategory, "id", None)
                subcategory_type = getattr(subcategory, "type", None)
                type_name = str(getattr(subcategory_type, "name", "")).upper()
                if type_name and type_name != "COMMON":
                    continue
                if category_id is None or subcategory_id is None:
                    continue
                row = categories.setdefault(
                    int(category_id),
                    {
                        "name": str(getattr(category, "name", None) or category_id),
                        "subcategories": set(),
                    },
                )
                row["subcategories"].add(int(subcategory_id))

            if not lots:
                return RaiseLotsResult("empty")
            if not categories:
                return RaiseLotsResult(
                    "error",
                    total_lots=len(lots),
                    errors=(("FunPay", "Не найдены категории лотов, доступные для поднятия"),),
                )

            raised = 0
            raised_categories = []
            errors = []
            for category_id, row in sorted(categories.items()):
                try:
                    self.account.raise_lots(
                        category_id,
                        sorted(row["subcategories"]),
                    )
                    raised += 1
                    raised_categories.append(str(row["name"]))
                except Exception as error:
                    reason = _raise_error_reason(error)
                    errors.append(
                        (
                            str(row["name"]),
                            reason,
                        )
                    )
                    logger.error(
                        "Не удалось поднять лоты категории %s: %s",
                        row["name"],
                        reason,
                    )

            status = "raised" if not errors else "partial" if raised else "error"
            return RaiseLotsResult(
                status,
                total_lots=len(lots),
                categories_total=len(categories),
                categories_raised=raised,
                raised_categories=tuple(raised_categories),
                errors=tuple(errors),
            )
        except Exception as error:
            reason = str(error).splitlines()[0].strip()
            logger.exception("Не удалось получить активные лоты для поднятия")
            return RaiseLotsResult(
                "error",
                errors=(("FunPay", reason[:300] if reason else type(error).__name__),),
            )
        finally:
            self._raise_lock.release()

    @staticmethod
    def lot_metadata(lot: Any) -> dict[str, Any]:
        subcategory = getattr(lot, "subcategory", None)
        category = getattr(subcategory, "category", None)
        return {
            "lot_id": str(getattr(lot, "id", "")),
            "title": str(getattr(lot, "title", None) or "Без названия"),
            "subcategory_id": getattr(subcategory, "id", None),
            "subcategory": str(getattr(subcategory, "name", None) or ""),
            "category": str(getattr(category, "name", None) or ""),
            "server": str(getattr(lot, "server", None) or ""),
            "price": getattr(lot, "price", None),
            "link": str(getattr(lot, "public_link", None) or ""),
        }

    def set_rule(self, lot: Any, text: str) -> None:
        metadata = self.lot_metadata(lot)
        metadata["text"] = text.strip()
        with self._lock:
            self._data["lots"][metadata["lot_id"]] = metadata
            self._save()

    def update_rule_text(self, lot_id: int | str, text: str) -> bool:
        with self._lock:
            rule = self._data["lots"].get(str(lot_id))
            if rule is None:
                return False
            rule["text"] = text.strip()
            self._save()
            return True

    def delete_rule(self, lot_id: int | str) -> bool:
        with self._lock:
            removed = self._data["lots"].pop(str(lot_id), None) is not None
            if removed:
                self._save()
            return removed

    def get_delivered_order_ids(self) -> set[str]:
        with self._lock:
            return {
                str(item.get("order_id") or "").upper()
                for item in self._data["delivered_orders"]
                if isinstance(item, dict) and item.get("order_id")
            }

    def was_delivered(self, order_id: str) -> bool:
        return str(order_id).lstrip("#").upper() in self.get_delivered_order_ids()

    def handle_event(self, event: Any) -> DeliveryResult | None:
        if type(event).__name__ != "NewOrderEvent" or self.account is None:
            return None
        shortcut = getattr(event, "order", None)
        if shortcut is None:
            return None
        status = getattr(getattr(shortcut, "status", None), "name", "")
        if status != "PAID":
            return None
        order_id = str(getattr(shortcut, "id", "") or "").lstrip("#").upper()
        if not order_id:
            return None

        with self._lock:
            delivered_ids = {
                str(item.get("order_id"))
                for item in self._data["delivered_orders"]
                if isinstance(item, dict)
            }
            if order_id in delivered_ids or order_id in self._in_progress:
                return None
            self._in_progress.add(order_id)

        try:
            order = self.account.get_order(order_id)
            if getattr(order, "seller_id", None) != getattr(self.account, "id", None):
                return None
            rule = self._match_rule(order)
            if rule is None:
                return None
            if rule == "ambiguous":
                logger.error("Неоднозначное правило автовыдачи для заказа #%s", order_id)
                return DeliveryResult("ambiguous", order_id)

            buyer_username = str(getattr(order, "buyer_username", None) or "покупатель")
            context = {
                "username": buyer_username,
                "order_id": order_id,
                "lot": getattr(order, "title", None) or "",
                "sum": getattr(order, "sum", None) or "",
                "amount": getattr(shortcut, "amount", None) or 1,
                "account": getattr(self.account, "username", None) or "",
            }
            delivery_text = render_template(str(rule["text"]), context)[:4000]
            if not delivery_text:
                return DeliveryResult(
                    "error", order_id, str(rule["lot_id"]), buyer_username, "Пустой текст автовыдачи"
                )
            chat = self.account.get_chat_by_name(buyer_username, make_request=True)
            if chat is None:
                return DeliveryResult(
                    "error", order_id, str(rule["lot_id"]), buyer_username, "Чат покупателя не найден"
                )
            self.account.send_message(chat.id, delivery_text, chat_name=buyer_username)
            with self._lock:
                self._data["delivered_orders"].append(
                    {
                        "order_id": order_id,
                        "lot_id": str(rule["lot_id"]),
                        "buyer_username": buyer_username,
                        "source": "bot",
                        "delivered_at": int(time.time()),
                    }
                )
                self._data["delivered_orders"] = self._data["delivered_orders"][-1000:]
                self._save()
            logger.info("Автовыдача выполнена для заказа #%s", order_id)
            return DeliveryResult("delivered", order_id, str(rule["lot_id"]), buyer_username)
        except Exception as error:
            logger.exception("Ошибка автовыдачи для заказа #%s", order_id)
            return DeliveryResult(
                "error", order_id, error=str(error).splitlines()[0][:300]
            )
        finally:
            with self._lock:
                self._in_progress.discard(order_id)

    def _match_rule(self, order: Any) -> dict[str, Any] | str | None:
        subcategory = getattr(order, "subcategory", None)
        subcategory_id = getattr(subcategory, "id", None)
        title = _normalize(getattr(order, "title", None))
        with self._lock:
            rules = copy.deepcopy(list(self._data["lots"].values()))
        matches = [
            rule
            for rule in rules
            if rule.get("subcategory_id") == subcategory_id
            and _normalize(rule.get("title")) == title
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return "ambiguous"
        return None
