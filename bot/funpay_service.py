from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from FunPayAPI import Account


class FunPayServiceError(Exception):
    pass


@dataclass(frozen=True)
class BalanceInfo:
    total_rub: float
    available_rub: float
    total_usd: float
    available_usd: float
    total_eur: float
    available_eur: float


@dataclass(frozen=True)
class ProfileInfo:
    user_id: int
    username: str
    profile_url: str
    avatar_url: str | None
    online: bool
    banned: bool
    active_sales: int
    active_purchases: int
    balance: BalanceInfo | None
    balance_error: str | None


def short_error(error: Exception) -> str:
    if type(error).__name__ == "UnauthorizedError":
        return "FunPay не принял SOURCE_GOLDEN_KEY"
    text = str(error).splitlines()[0].strip()
    return text[:200] if text else type(error).__name__


def read_balance(account: Any, profile: Any) -> BalanceInfo:
    common_lots = profile.get_common_lots()
    if not common_lots:
        raise FunPayServiceError("нет обычного лота для открытия формы оплаты")

    balance = account.get_balance(common_lots[0].id)
    return BalanceInfo(
        total_rub=balance.total_rub,
        available_rub=balance.available_rub,
        total_usd=balance.total_usd,
        available_usd=balance.available_usd,
        total_eur=balance.total_eur,
        available_eur=balance.available_eur,
    )


class FunPayService:
    def __init__(self, golden_key: str):
        self.golden_key = golden_key

    def get_profile(self) -> ProfileInfo:
        try:
            account = Account(self.golden_key).get()
            if account.id is None:
                raise FunPayServiceError("FunPayAPI не вернул ID аккаунта")
            profile = account.get_user(account.id)
        except Exception as error:
            raise FunPayServiceError(short_error(error)) from error

        balance = None
        balance_error = None
        try:
            balance = read_balance(account, profile)
        except Exception as error:
            balance_error = short_error(error)

        return ProfileInfo(
            user_id=profile.id,
            username=profile.username,
            profile_url=f"https://funpay.com/users/{profile.id}/",
            avatar_url=profile.profile_photo or None,
            online=profile.online,
            banned=profile.banned,
            active_sales=account.active_sales or 0,
            active_purchases=account.active_purchases or 0,
            balance=balance,
            balance_error=balance_error,
        )
