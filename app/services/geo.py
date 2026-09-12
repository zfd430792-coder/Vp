"""Города и расстояния.

Бот не обращается к внешним картам: подсказки берутся из встроенного справочника
крупных городов, а точные координаты приходят только от самого человека, если он
поделился местоположением. Всё, что мы храним, округлено — расстояние нужно
показать примерно, а не вычислить чей-то адрес.
"""
from __future__ import annotations

import math
import re
from typing import NamedTuple

from app.data.cities import CITIES, City
from app.utils.text import ValidationError, clean_city

# «г. Москва», «город Сочи», «пос. Заря» — приставку убираем, но только целым словом,
# иначе пострадали бы Горно-Алтайск и подобные названия
_PREFIX_RE = re.compile(
    r"^(г|гор|город|пос|поселок|посёлок|пгт|село|с|деревня|д|ст|станица)\.?\s+",
    re.IGNORECASE,
)
_LETTERS_RE = re.compile(r"[^a-zа-я]+")

# Точность хранения координат: сотые градуса — это примерно километр.
# Хватает, чтобы показать «~15 км», и не превращает базу в карту адресов.
COORD_PRECISION = 2

# Насколько далеко имеет смысл искать город по присланной точке
NEAREST_LIMIT_KM = 150

EARTH_RADIUS_KM = 6371.0
DEGREE_KM = 111.2


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = _PREFIX_RE.sub("", str(text).strip())
    value = value.lower().replace("ё", "е")
    return _LETTERS_RE.sub("", value)


_BY_KEY: dict[str, City] = {}
_BY_REGION: list[tuple[str, City]] = []

for _city in CITIES:
    _BY_KEY.setdefault(normalize(_city.name), _city)
    for _alias in _city.aliases:
        _BY_KEY.setdefault(normalize(_alias), _city)
    if _city.region:
        _BY_REGION.append((normalize(_city.region), _city))
    _BY_REGION.append((normalize(_city.country), _city))


def by_name(name: str | None) -> City | None:
    """Точное совпадение по названию или псевдониму."""
    return _BY_KEY.get(normalize(name))


def find(query: str | None, limit: int = 8) -> list[City]:
    """Подбирает города по тому, что человек написал.

    Порядок: точное совпадение, начало названия, часть названия, регион или страна.
    Внутри группы сначала идут более крупные города.
    """
    key = normalize(query)
    if len(key) < 2:
        return []

    exact = _BY_KEY.get(key)
    if exact is not None:
        return [exact]

    def rank(city: City) -> tuple[int, int, str]:
        return (city.weight, len(city.name), city.name)

    starts = [city for city in CITIES if normalize(city.name).startswith(key)]
    if not starts and len(key) >= 4:
        starts = [
            city
            for city in CITIES
            if any(normalize(alias).startswith(key) for alias in city.aliases)
        ]

    inside: list[City] = []
    if len(key) >= 4:
        inside = [
            city
            for city in CITIES
            if city not in starts and key in normalize(city.name)
        ]

    regional: list[City] = []
    if len(key) >= 4:
        seen = set(starts) | set(inside)
        regional = [
            city
            for region_key, city in _BY_REGION
            if city not in seen and region_key.startswith(key)
        ]

    result: list[City] = []
    for group in (sorted(starts, key=rank), sorted(inside, key=rank), sorted(regional, key=rank)):
        for city in group:
            if city not in result:
                result.append(city)
            if len(result) >= limit:
                return result
    return result


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по большому кругу, километры."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def nearest(lat: float, lon: float, limit_km: float = NEAREST_LIMIT_KM) -> City | None:
    """Ближайший город справочника к присланной точке."""
    best: City | None = None
    best_km = limit_km
    for city in CITIES:
        km = distance_km(lat, lon, city.lat, city.lon)
        if km < best_km:
            best, best_km = city, km
    return best


def round_coords(lat: float, lon: float) -> tuple[float, float]:
    """Округляет координаты перед записью в базу."""
    return (round(float(lat), COORD_PRECISION), round(float(lon), COORD_PRECISION))


def format_distance(km: float) -> str:
    """Человеческое расстояние. Ближе пяти километров точность не показываем."""
    if km < 5:
        return "рядом"
    if km < 50:
        return f"~{int(round(km / 5) * 5)} км"
    if km < 500:
        return f"~{int(round(km / 10) * 10)} км"
    return f"~{int(round(km / 50) * 50)} км"


class Resolution(NamedTuple):
    """Что делать с тем, что человек написал в поле города.

    kind: city — нашли один город, choice — несколько подходящих,
    free — в справочнике нет, но текст годится как есть, invalid — текст не годится.
    """

    kind: str
    city: City | None = None
    matches: tuple[City, ...] = ()
    name: str = ""
    error: str = ""


def resolve_input(text: str | None) -> Resolution:
    """Разбирает введённый город: точное совпадение, выбор или свободный текст."""
    raw = (text or "").strip()
    if not raw:
        return Resolution("invalid", error="Название города не может быть пустым.")

    matches = find(raw)
    if len(matches) == 1:
        return Resolution("city", city=matches[0])
    if matches:
        return Resolution("choice", matches=tuple(matches))

    try:
        name = clean_city(raw)
    except ValidationError as error:
        return Resolution("invalid", error=str(error))
    return Resolution("free", name=name)


def cos_lat(lat: float) -> float:
    """Коэффициент сжатия долготы на этой широте.

    Нужен, чтобы сравнивать расстояния прямо в SQL обычной арифметикой,
    без тригонометрии: она есть не в каждой сборке SQLite.
    """
    return math.cos(math.radians(lat))
