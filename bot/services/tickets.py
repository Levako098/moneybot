from __future__ import annotations

import html
import json
import logging
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger("moneybot.tickets")
_RATE_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL = 0.0
_LAST_REQUEST_AT = 0.0
_MIN_REQUEST_INTERVAL = 4.0
_DEFAULT_COOLDOWN = 600.0


class TicketError(Exception):
    pass


class SupportRateLimitedError(TicketError):
    pass


def _before_support_request() -> None:
    global _LAST_REQUEST_AT
    with _RATE_LOCK:
        now = time.time()
        if now < _RATE_LIMIT_UNTIL:
            wait_left = max(1, int(_RATE_LIMIT_UNTIL - now))
            raise SupportRateLimitedError(
                f"FunPay Support временно ограничил запросы. Осталось {wait_left} сек."
            )
        delay = _MIN_REQUEST_INTERVAL - (now - _LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_AT = time.time()


def _check_rate_limit(response: requests.Response) -> None:
    global _RATE_LIMIT_UNTIL
    if response.status_code != 429:
        return
    try:
        cooldown = max(60.0, float(response.headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        cooldown = _DEFAULT_COOLDOWN
    with _RATE_LOCK:
        _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, time.time() + cooldown)
    raise SupportRateLimitedError(
        f"FunPay Support временно ограничил запросы. Повторите через {int(cooldown)} сек."
    )


class TicketClient:
    def __init__(
        self,
        golden_key: str,
        phpsessid: str = "",
        timeout: int = 20,
    ) -> None:
        self.golden_key = golden_key
        self.phpsessid = phpsessid
        self.timeout = timeout
        self.csrf_token = ""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
                )
            }
        )

    def _extract_csrf(self, page: str) -> str:
        soup = BeautifulSoup(page or "", "html.parser")
        body = soup.find("body")
        if body and body.get("data-app-config"):
            try:
                token = json.loads(body["data-app-config"]).get("csrfToken")
                if token:
                    return str(token)
            except (TypeError, ValueError):
                pass
        for attrs in (
            {"name": "ticket[_token]"},
            {"name": "_token"},
            {"name": "csrf_token"},
        ):
            field = soup.find("input", attrs=attrs)
            if field and field.get("value"):
                return str(field["value"])
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        return str(meta.get("content") or "") if meta else ""

    @staticmethod
    def _looks_unauthorized(response: requests.Response, page: str) -> bool:
        url = str(response.url or "").lower()
        text = (page or "").lower()
        return (
            "/account/login" in url
            or "/support/sso" in url
            or "<title>войти" in text
            or ('data-app-data' in text and '"userid":0' in text)
        )

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        exclude_phpsessid: bool = False,
    ) -> requests.Response:
        self.session.cookies.set("golden_key", self.golden_key, domain="funpay.com")
        self.session.cookies.set("cookie_prefs", "1", domain="funpay.com")
        if self.phpsessid and not exclude_phpsessid:
            self.session.cookies.set(
                "PHPSESSID", self.phpsessid, domain="support.funpay.com"
            )

        current_url = url
        response = None
        for _ in range(10):
            if "support.funpay.com" in current_url:
                _before_support_request()
            request = requests.Request(
                method.upper(), current_url, headers=headers or {}, data=payload or {}
            )
            prepared = self.session.prepare_request(request)
            settings = self.session.merge_environment_settings(
                prepared.url, {}, None, None, None
            )
            settings.update({"timeout": self.timeout, "allow_redirects": False})
            response = self.session.send(prepared, **settings)
            _check_rate_limit(response)
            location = response.headers.get("Location", "")
            if not (300 <= response.status_code < 400 and location and location != "/"):
                return response
            current_url = (
                location if location.startswith("http") else urljoin(current_url, location)
            )
        if response is None:
            raise TicketError("FunPay Support не вернул ответ")
        return response

    def _auth(self, return_to: str) -> None:
        self.session.cookies.clear()
        self.session.cookies.set("golden_key", self.golden_key, domain="funpay.com")
        self.session.cookies.set("cookie_prefs", "1", domain="funpay.com")

        response = self.session.get(
            f"https://funpay.com/support/sso?return_to={return_to}",
            timeout=self.timeout,
            allow_redirects=False,
        )
        if response.status_code not in (301, 302):
            raise TicketError(f"FunPay SSO вернул HTTP {response.status_code}")
        redirect = response.headers.get("Location", "")
        jwt = parse_qs(urlparse(redirect).query).get("jwt", [None])[0]
        if not jwt:
            raise TicketError("FunPay не выдал SSO-токен")

        _before_support_request()
        response = self.session.get(
            f"https://support.funpay.com/access/jwt?jwt={jwt}&return_to={return_to}",
            timeout=self.timeout,
            allow_redirects=False,
        )
        _check_rate_limit(response)
        if response.status_code not in (200, 301, 302):
            raise TicketError(f"Support SSO вернул HTTP {response.status_code}")

        self.phpsessid = ""
        for cookie in self.session.cookies:
            if cookie.name == "PHPSESSID" and "support.funpay.com" in cookie.domain:
                self.phpsessid = cookie.value
                break
        if not self.phpsessid:
            raise TicketError("Не удалось получить support PHPSESSID")

    def _ensure_auth(self, write: bool = False) -> None:
        if self.phpsessid:
            response = self._request("get", "https://support.funpay.com/tickets/")
            page = response.text or ""
            if response.status_code == 200 and not self._looks_unauthorized(response, page):
                self.csrf_token = self._extract_csrf(page)
                if not write or self.csrf_token:
                    return

        self.phpsessid = ""
        return_to = "%2Ftickets%2Fnew%2F1" if write else "%2Ftickets%2F"
        self._auth(return_to)
        response = self._request(
            "get",
            "https://support.funpay.com/tickets/new/1"
            if write
            else "https://support.funpay.com/tickets/",
        )
        response.raise_for_status()
        self.csrf_token = self._extract_csrf(response.text)
        if write and not self.csrf_token:
            raise TicketError("Не удалось получить CSRF-токен Support")

    def get_tickets(self) -> list[dict[str, Any]]:
        self._ensure_auth()
        response = self._request(
            "get",
            "https://support.funpay.com/tickets/",
            {"X-CSRF-Token": self.csrf_token},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items = soup.select(".ticket-list a.ticket-item") or soup.select("a.ticket-item")
        tickets = []
        for item in items:
            href = str(item.get("href") or "")
            ticket_id = href.rstrip("/").split("/")[-1]
            if not ticket_id.isdigit():
                continue
            row = item.find("div", class_="row") or item
            subject_nodes = row.find_all(
                "div", class_=lambda value: value and "col-12" in value
            )
            badge = row.find("span", class_="badge")
            date_node = row.find("span", class_="text-secondary")
            status_label = badge.get_text(" ", strip=True) if badge else ""
            tickets.append(
                {
                    "id": ticket_id,
                    "subject": (
                        subject_nodes[-1].get_text(" ", strip=True)
                        if subject_nodes
                        else f"Тикет #{ticket_id}"
                    ),
                    "status": status_label,
                    "date": date_node.get_text(" ", strip=True) if date_node else "",
                    "unread": "unread" in (item.get("class") or []),
                }
            )
        return tickets

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        if not str(ticket_id).isdigit():
            raise TicketError("Некорректный ID тикета")
        self._ensure_auth()
        response = self._request(
            "get",
            f"https://support.funpay.com/tickets/{ticket_id}",
            {"X-CSRF-Token": self.csrf_token},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        subject = f"Тикет #{ticket_id}"
        status = ""
        panel = soup.find(class_=lambda value: value and "ticket-info-panel" in value)
        if panel:
            lines = [x.strip() for x in panel.get_text("\n", strip=True).splitlines()]
            lines = [x for x in lines if x]
            for index, line in enumerate(lines[:-1]):
                if line.lower() == "тема заявки":
                    subject = lines[index + 1]
                elif line.lower() == "статус":
                    status = lines[index + 1]

        messages = []
        for element in soup.select(".ticket-comment"):
            author_element = element.find(
                class_=lambda value: value and "comment-author" in value
            )
            username_element = (
                author_element.find(class_="username") if author_element else None
            )
            author = (
                username_element.get_text(" ", strip=True)
                if username_element
                else "FunPay Support"
            )
            body_element = element.find(
                class_=lambda value: value and "comment-text" in value
            )
            body = body_element.get_text("\n", strip=True) if body_element else ""
            time_element = element.find("time")
            date = (
                str(time_element.get("datetime") or time_element.get_text(strip=True))
                if time_element
                else ""
            )
            if body:
                messages.append({"author": author, "body": body[:4000], "date": date})
        return {"id": ticket_id, "subject": subject, "status": status, "messages": messages}

    def send_ticket(
        self,
        message: str,
        username: str = "",
        order_id: str = "",
        form_id: str = "1",
        topic_id: str = "202",
        role: str = "seller",
    ) -> str:
        message = message.strip()
        if not message:
            raise TicketError("Текст тикета пуст")
        if form_id == "1" and not order_id.strip():
            raise TicketError("Для тикета по сделке нужен номер заказа")
        self._ensure_auth(write=True)
        page = self._request(
            "get", f"https://support.funpay.com/tickets/new/{form_id}"
        )
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        token_field = soup.find("input", attrs={"name": "ticket[_token]"})
        if not token_field or not token_field.get("value"):
            raise TicketError("Не удалось получить ticket[_token]")

        payload = {
            "ticket[fields][1]": username or "user",
            "ticket[fields][2]": order_id.strip(),
            "ticket[comment][body_html]": f"<p>{html.escape(message)}</p>",
            "ticket[comment][attachments]": "",
            "ticket[_token]": token_field["value"],
        }
        if form_id == "3":
            payload["ticket[fields][7]"] = topic_id
        else:
            role_value = "1" if role == "buyer" else "2"
            topic_field = "4" if role == "buyer" else "5"
            payload["ticket[fields][3]"] = role_value
            payload[f"ticket[fields][{topic_field}]"] = topic_id
        response = self._request(
            "post",
            f"https://support.funpay.com/tickets/create/{form_id}",
            {
                "Origin": "https://support.funpay.com",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": self.csrf_token,
                "Referer": f"https://support.funpay.com/tickets/new/{form_id}",
            },
            payload,
        )
        if response.status_code >= 400:
            detail = ""
            try:
                error_data = response.json()
                detail = str(
                    error_data.get("message")
                    or error_data.get("error")
                    or error_data
                )
            except (TypeError, ValueError):
                detail = BeautifulSoup(response.text, "html.parser").get_text(
                    " ", strip=True
                )
            detail = " ".join(detail.split())[:300]
            suffix = f": {detail}" if detail else ""
            raise TicketError(f"Support вернул HTTP {response.status_code}{suffix}")
        try:
            result = response.json()
        except ValueError as error:
            raise TicketError("Support вернул некорректный ответ") from error
        if result.get("error"):
            raise TicketError(str(result.get("message") or result.get("error"))[:300])
        ticket_id = str(result.get("id") or result.get("ticket_id") or "")
        return ticket_id
