"""Общие константы и значения по умолчанию."""
from __future__ import annotations

DAY = 86400
HOUR = 3600
MINUTE = 60

MIN_AGE = 18
MAX_AGE = 99

NAME_MAX_LEN = 32
BIO_MAX_LEN = 400
CITY_MAX_LEN = 40
MAX_PHOTOS = 3
MIN_PHOTOS = 1
MAX_INTERESTS = 5

# Уровни доступа
ROLE_USER = 0
ROLE_MODERATOR = 1
ROLE_ADMIN = 2
ROLE_OWNER = 3

ROLE_NAMES = {
    ROLE_USER: "пользователь",
    ROLE_MODERATOR: "модератор",
    ROLE_ADMIN: "администратор",
    ROLE_OWNER: "владелец",
}

# Статусы пользователя
STATUS_NEW = "new"
STATUS_ACTIVE = "active"
STATUS_BANNED = "banned"
STATUS_DELETED = "deleted"

# Статусы модерации анкеты
MOD_OK = "ok"          # всё хорошо
MOD_REVIEW = "review"  # видна в поиске, но с пониженным охватом, ждёт проверки
MOD_HOLD = "hold"      # скрыта до решения модератора
MOD_REJECTED = "rejected"  # отклонена, нужно исправить

# Действия в ленте
ACT_LIKE = "like"
ACT_SUPERLIKE = "superlike"
ACT_PASS = "pass"

GENDERS = {"m": "парень", "f": "девушка"}
GENDER_ICONS = {"m": "👨", "f": "👩"}
SEEKING = {"m": "парней", "f": "девушек", "any": "всех"}

# Мягкие ограничения охвата (теневой бан «по-человечески»)
SHADOW_REACH = {0: 100, 1: 70, 2: 40, 3: 15}
SHADOW_TITLES = {
    1: "лёгкое ограничение",
    2: "среднее ограничение",
    3: "сильное ограничение",
}

# Верификация анкеты: живое селфи с заданным жестом
VERIFY_NONE = "none"
VERIFY_PENDING = "pending"
VERIFY_OK = "ok"
VERIFY_REJECTED = "rejected"

VERIFY_GESTURES: tuple[str, ...] = (
    "подними вверх большой палец 👍",
    "покажи два пальца ✌️",
    "покажи открытую ладонь 🖐",
    "приложи ладонь к щеке",
    "сложи руки в сердечко 🫶",
    "покажи кулак 👊",
)
VERIFY_ATTEMPTS_PER_DAY = 3
VERIFY_RETRY_COOLDOWN = 3600

# Из чего складывается суточный лимит лайков
BONUS_FULL_PROFILE = 10   # 2+ фото и живое описание
BONUS_VERIFIED = 20       # пройдена верификация
BONUS_TRUSTED = 10        # высокое доверие и не новый аккаунт
PENALTY_FRESH = 10        # аккаунт младше суток
PENALTY_LOW_TRUST = 10    # доверие ниже порога
MIN_LIKES_LIMIT = 5       # меньше этого лимит не опускается

INTERESTS: dict[str, str] = {
    "music": "🎧 Музыка",
    "movies": "🎬 Кино",
    "sport": "🏋️ Спорт",
    "travel": "✈️ Путешествия",
    "games": "🎮 Игры",
    "books": "📚 Книги",
    "food": "🍜 Еда",
    "art": "🎨 Искусство",
    "tech": "💻 Технологии",
    "cars": "🚗 Авто",
    "pets": "🐾 Животные",
    "nature": "🌲 Природа",
    "photo": "📷 Фото",
    "dance": "💃 Танцы",
    "science": "🔬 Наука",
    "business": "📈 Бизнес",
}

REPORT_CATEGORIES: dict[str, str] = {
    "scam": "💸 Мошенничество, попрошайничество",
    "blackmail": "🔒 Шантаж, угрозы, слив фото",
    "fake": "🎭 Фейк, чужие фотографии",
    "ads": "📢 Реклама, продажа услуг",
    "nsfw": "🔞 Непристойный контент",
    "minor": "🚸 Несовершеннолетний",
    "abuse": "🤬 Оскорбления, агрессия",
    "other": "❓ Другое",
}

# Приоритет жалобы: чем выше, тем раньше в очереди модерации
REPORT_PRIORITY: dict[str, int] = {
    "minor": 100,
    "blackmail": 90,
    "scam": 70,
    "nsfw": 60,
    "fake": 40,
    "abuse": 40,
    "ads": 20,
    "other": 10,
}

# Настройки, которые можно менять из админ-панели
DEFAULT_SETTINGS: dict[str, str] = {
    "likes_per_day": "60",          # лимит лайков за 24 часа
    "likes_per_day_new": "30",      # лимит для новичков и аккаунтов с низким доверием
    "superlikes_per_day": "3",      # лимит суперлайков за 24 часа
    "pass_ttl_days": "14",          # через сколько дней пропущенная анкета снова покажется
    "inactive_days": "45",          # после скольких дней без захода анкета скрывается из поиска
    "reports_for_review": "2",      # столько уникальных жалоб — анкета уходит на проверку
    "reports_for_shadow": "3",      # столько уникальных жалоб — мягкое ограничение охвата
    "reports_for_hold": "5",        # столько уникальных жалоб — анкета скрывается до решения модератора
    "shadow_hours": "24",           # длительность автоматического ограничения охвата
    "registration_open": "1",       # 1 — регистрация открыта, 0 — закрыта
    "captcha_enabled": "1",         # капча для подозрительных регистраций
    "reg_burst_limit": "12",        # столько регистраций за час включает капчу для всех новых
    "report_sla_hours": "6",        # через сколько часов без ответа жалоба считается просроченной
    "message_keep_days": "30",      # сколько дней хранится переписка для разбора жалоб
    "min_trust_for_full_limits": "40",
    "trust_for_bonus": "70",        # доверие, с которого даётся бонус к лимиту
    "verification_enabled": "1",    # можно ли подтверждать анкету селфи
    "fresh_profile_boost_days": "3",  # сколько дней новые анкеты получают приоритет
    "nudge_enabled": "1",           # напоминать ли о непросмотренных лайках
    "nudge_after_days": "3",        # через сколько дней молчания можно напомнить
    "nudge_every_days": "7",        # не чаще одного напоминания в эти дни
    "nudge_batch": "200",           # сколько напоминаний за один проход
}
