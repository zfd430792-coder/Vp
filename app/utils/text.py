"""Работа с текстом: экранирование, валидация, эвристики на спам."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata

from app.constants import BIO_MAX_LEN, CITY_MAX_LEN, MAX_AGE, MIN_AGE, NAME_MAX_LEN

# --------------------------------------------------------------------------- базовое


def esc(value: object) -> str:
    """Экранирует текст для parse_mode=HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def plural(number: int, one: str, few: str, many: str) -> str:
    """Русские окончания: 1 лайк, 2 лайка, 5 лайков."""
    number = abs(int(number))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many


def human_delta(seconds: int) -> str:
    """Человекочитаемый интервал: «2 ч 15 мин»."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} сек"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days} {plural(days, 'день', 'дня', 'дней')}")
    if hours:
        parts.append(f"{hours} ч")
    if minutes and not days:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{secs} сек")
    return " ".join(parts)


def shorten(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def text_hash(text: str) -> str:
    """Хэш нормализованного текста — для поиска одинаковых анкет у разных аккаунтов."""
    normalized = re.sub(r"[^0-9a-zа-яё]+", "", (text or "").lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def strip_invisible(text: str) -> str:
    """Убирает управляющие и невидимые символы, которыми маскируют спам."""
    cleaned = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Co", "Cs"} and char not in "\n\t":
            continue
        cleaned.append(char)
    return "".join(cleaned)


# --------------------------------------------------------------------------- валидация


class ValidationError(ValueError):
    """Ошибка пользовательского ввода с готовым текстом для ответа."""


_NAME_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё \-'`.]+$")
_CITY_RE = re.compile(r"^[A-Za-zА-Яа-яЁё \-.']+$")


def clean_name(raw: str) -> str:
    value = strip_invisible(raw or "").strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise ValidationError("Имя не может быть пустым. Напиши, как тебя зовут.")
    if len(value) < 2:
        raise ValidationError("Слишком короткое имя — нужно минимум 2 символа.")
    if len(value) > NAME_MAX_LEN:
        raise ValidationError(f"Слишком длинное имя. Максимум {NAME_MAX_LEN} символа.")
    if not _NAME_RE.match(value):
        raise ValidationError("В имени можно использовать только буквы, цифры, пробел и дефис.")
    if has_contacts(value):
        raise ValidationError("В имени нельзя указывать ссылки, ники и номера телефонов.")
    return value


def clean_age(raw: str) -> int:
    value = (raw or "").strip()
    if not value.isdigit():
        raise ValidationError("Возраст нужно написать числом. Например: 23")
    age = int(value)
    if age < MIN_AGE:
        raise ValidationError(
            f"Бот работает только с {MIN_AGE}+. Если тебе меньше — возвращайся, когда подрастёшь."
        )
    if age > MAX_AGE:
        raise ValidationError(f"Максимальный возраст — {MAX_AGE}. Проверь, пожалуйста, число.")
    return age


def clean_city(raw: str) -> str:
    value = strip_invisible(raw or "").strip()
    value = re.sub(r"^(г\.?|гор\.?|город)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value)
    if len(value) < 2:
        raise ValidationError("Название города слишком короткое.")
    if len(value) > CITY_MAX_LEN:
        raise ValidationError(f"Название города слишком длинное. Максимум {CITY_MAX_LEN} символов.")
    if not _CITY_RE.match(value):
        raise ValidationError("В названии города можно использовать только буквы, пробел и дефис.")
    return value.title()


def normalize_city(city: str) -> str:
    value = (city or "").lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я]+", "", value)
    return value


def clean_bio(raw: str) -> str:
    value = strip_invisible(raw or "").strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    if len(value) > BIO_MAX_LEN:
        raise ValidationError(
            f"Описание длиннее {BIO_MAX_LEN} символов (сейчас {len(value)}). Сократи немного."
        )
    return value


# --------------------------------------------------------------------------- эвристики спама

_URL_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|tg://)|"
    r"\b[a-z0-9-]{2,}\.(ru|com|net|org|io|me|xyz|biz|info|shop|site|online|top|club|link|app)\b",
    re.IGNORECASE,
)
_MENTION_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\-\s()]{8,17}\d)(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}(?!\d)")

_SPAM_WORDS = (
    "заработ", "инвест", "крипт", "бинанс", "binance", "usdt", "трейд", "ставк",
    "казино", "букмекер", "бонус на депозит", "пассивный доход", "схема заработка",
    "вебкам", "эскорт", "интим услуг", "интим-услуг", "за донат", "оплата картой",
    "подпишись", "подписывайся", "мой канал", "мой тг", "переходи по ссылке",
    "реферальн", "млм", "работа в интернете", "набираю команду", "нужны девушки",
)
_LURE_WORDS = (
    "я не сижу тут", "тут не отвечаю", "мой номер", "скину номер",
    "напиши мне на", "добавь меня в", "найди меня в",
)

# Увод в другой мессенджер — частый приём мошенников и ботоферм
_MESSENGER_RE = re.compile(
    r"\b(вотсап|ватсап|whats\s?app|вайбер|viber|инстаграм|instagram|инста|"
    r"снапчат|snapchat|вконтакте|vk\.com|вк\b|скайп|skype|дискорд|discord)",
    re.IGNORECASE,
)


def has_contacts(text: str) -> bool:
    """Есть ли в тексте ссылка, ник, телефон или номер карты."""
    if not text:
        return False
    value = strip_invisible(text)
    return bool(
        _URL_RE.search(value)
        or _MENTION_RE.search(value)
        or _PHONE_RE.search(value)
        or _CARD_RE.search(value)
    )


def analyze_text(text: str) -> tuple[int, list[str]]:
    """Оценивает текст на спам.

    Возвращает (оценка риска 0..100, список причин).
    """
    if not text or not text.strip():
        return 0, []
    value = strip_invisible(text)
    lower = value.lower().replace("ё", "е")
    score = 0
    reasons: list[str] = []

    if _URL_RE.search(value):
        score += 45
        reasons.append("ссылка в тексте")
    if _MENTION_RE.search(value):
        score += 35
        reasons.append("упоминание другого аккаунта")
    if _PHONE_RE.search(value):
        score += 35
        reasons.append("номер телефона")
    if _CARD_RE.search(value):
        score += 60
        reasons.append("похоже на номер карты")

    hits = [word for word in _SPAM_WORDS if word in lower]
    if hits:
        score += min(50, 20 * len(hits))
        reasons.append("рекламные слова: " + ", ".join(hits[:3]))

    lure_hits = [word for word in _LURE_WORDS if word in lower]
    if lure_hits:
        score += 25
        reasons.append("зовёт писать в другое место")

    if _MESSENGER_RE.search(lower):
        score += 20
        reasons.append("упоминание другого мессенджера")

    letters = [char for char in value if char.isalpha()]
    if len(letters) >= 12:
        caps_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
        if caps_ratio > 0.75:
            score += 15
            reasons.append("текст капсом")

    if re.search(r"(.)\1{6,}", value):
        score += 10
        reasons.append("повторяющиеся символы")

    return min(100, score), reasons
