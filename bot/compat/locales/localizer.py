from __future__ import annotations

from typing import Any


class Localizer:
    def __init__(self, *_: Any, **__: Any) -> None:
        self.languages = {"ru": "Русский", "en": "English", "uk": "Українська"}

    @staticmethod
    def translate(key: str, *args: Any, language: str | None = None, **kwargs: Any) -> str:
        del language
        if args or kwargs:
            try:
                return key.format(*args, **kwargs)
            except (IndexError, KeyError, ValueError):
                pass
        return key
