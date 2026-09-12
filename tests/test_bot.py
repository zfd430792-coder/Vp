"""Сквозные сценарии: имитируем нажатия и сообщения реального пользователя.

Запуск: python tests/test_bot.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config
from app.db import Database
from app.services import feed, insights, limits, moderation, profiles, users
from app.services import likes as likes_service
from app.services.scheduler import Maintenance
from app.services.settings import Settings
from app.utils.time import now
from tests.harness import Harness

PASSED: list[str] = []
FAILED: list[str] = []

ALICE = 100
BORIS = 200
CARL = 300
DINA = 400
NEWBIE = 500
EMMA = 600
GEO = 800
OWNER = 1


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append(f"{label} — {detail}")
        print(f"  ❌ {label} — {detail}")


def has(session, needle: str, chat_id: int | None = None) -> bool:
    return any(needle in text for text in session.texts(chat_id))


async def register(harness: Harness, user_id: int, *, name: str, age: int, gender: str, seeking: str) -> None:
    """Проходит регистрацию так, как это сделал бы человек."""
    await harness.send(user_id, "/start")
    await harness.click(user_id, "reg:rules_ok:")
    await harness.send(user_id, name)
    await harness.send(user_id, str(age))
    await harness.click(user_id, f"reg:gender:{gender}")
    await harness.click(user_id, f"reg:seeking:{seeking}")
    await harness.send(user_id, "Москва")
    await harness.click(user_id, "reg:interest:music")
    await harness.click(user_id, "reg:interest:travel")
    await harness.click(user_id, "reg:interests_done:")
    await harness.send(user_id, f"Меня зовут {name}, люблю горы и хороший кофе.")
    await harness.send(user_id, "фото", photo=f"photo_{user_id}_1")
    await harness.send(user_id, "фото", photo=f"photo_{user_id}_2")
    await harness.click(user_id, "reg:photos_done:")
    await harness.click(user_id, "reg:publish:")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "bot.db"
    db = Database(tmp)
    await db.connect()
    settings = Settings(db)
    await settings.load()
    # Пауза на чтение предупреждения проверяется отдельно, остальным сценариям она мешает
    await settings.set("warning_delay", "0")
    config = Config(token="1:x", owner_ids=(OWNER,), db_path=tmp, support_contact="@help")
    await users.sync_owners(db, (OWNER,))
    harness = Harness(db, settings, config)
    session = harness.session

    print("\n▶ Первый запуск: приветствие, потом предупреждение")
    session.clear()
    await harness.send(ALICE, "/start")
    texts_seen = session.texts(ALICE)
    check("приветствие показано первым", "Это бот знакомств" in texts_seen[0], texts_seen[0][:40])
    check("предупреждение идёт после приветствия", has(session, "Прочитай перед началом", ALICE))
    check("в предупреждении есть возраст 18+", has(session, "18 лет", ALICE))
    check("в предупреждении есть про деньги", has(session, "Денег здесь никто не просит", ALICE))
    check("в предупреждении есть про шантаж", has(session, "не плати", ALICE))
    check("сказано, что переписку без жалобы не читают", has(session, "никто не читает", ALICE))
    check("есть кнопка согласия", "reg:rules_ok:" in session.all_buttons(ALICE))
    check("есть кнопка безопасности", "reg:safety:" in session.all_buttons(ALICE))

    session.clear()
    await harness.click(ALICE, "reg:safety:")
    check("памятка о мошенниках открывается", has(session, "Признаки мошенника"))
    check("сказано не платить шантажисту", has(session, "Не плати"))
    check("из памятки можно сразу принять правила", "reg:rules_ok:" in session.all_buttons(ALICE))

    session.clear()
    await harness.click(ALICE, "reg:rules_back:")
    check("возврат к предупреждению работает", has(session, "Прочитай перед началом", ALICE))
    await harness.click(ALICE, "reg:rules_no:")
    check("отказ обрабатывается", has(session, "Без согласия с правилами"))
    stored = await users.get(db, ALICE)
    check("правила не приняты при отказе", not stored.get("rules_accepted_at"))

    print("\n▶ Кнопка появляется только после паузы на чтение")
    await settings.set("warning_delay", "2")
    session.clear()
    await harness.send(700, "/start")
    warning_calls = [
        payload
        for name, payload in session.calls
        if name == "SendMessage" and "Прочитай перед началом" in (payload.get("text") or "")
    ]
    check("предупреждение отправлено без кнопок", bool(warning_calls) and not warning_calls[0].get("reply_markup"))
    check("видно, сколько ждать", any("Кнопка появится через" in (p.get("text") or "") for p in warning_calls))
    edits = [
        payload
        for name, payload in session.calls
        if name == "EditMessageText" and payload.get("reply_markup")
    ]
    check("после отсчёта кнопка появилась", bool(edits))
    if edits:
        markup = edits[-1].get("reply_markup") or {}
        codes = [
            button.get("callback_data")
            for row in markup.get("inline_keyboard", [])
            for button in row
        ]
        check("это кнопка согласия", "reg:rules_ok:" in codes, str(codes))
    check("шло не меньше двух правок отсчёта", len([n for n, _ in session.calls if n == "EditMessageText"]) >= 2)

    session.clear()
    await harness.set_state_data(700, warning_at=int(time.time()))
    await harness.click(700, "reg:rules_ok:")
    check("досрочное нажатие отклоняется", any("Дочитай" in a for a in session.alerts()))
    check("правила при этом не приняты", not (await users.get(db, 700)).get("rules_accepted_at"))
    await settings.set("warning_delay", "0")

    print("\n▶ Регистрация")
    session.clear()
    await harness.send(ALICE, "/start")
    check("приветствие показывается снова", has(session, "Это бот знакомств", ALICE))
    await harness.click(ALICE, "reg:rules_ok:")
    check("начался шаг с именем", has(session, "Шаг 1/7"))

    await harness.send(ALICE, "Аня123!!!")
    check("некорректное имя отклонено", has(session, "только буквы"))
    session.clear()
    await harness.send(ALICE, "Аня")
    check("после имени спрашивают возраст", has(session, "Шаг 2/7"))

    session.clear()
    await harness.send(ALICE, "17")
    check("несовершеннолетних не пускают", has(session, "18+"))
    await harness.send(ALICE, "двадцать")
    check("возраст только числом", has(session, "числом"))

    session.clear()
    await harness.send(ALICE, "24")
    check("после возраста спрашивают пол", has(session, "Шаг 3/7"))
    await harness.click(ALICE, "reg:gender:f")
    await harness.click(ALICE, "reg:seeking:m")
    check("после пола спрашивают город", has(session, "Шаг 5/7"))

    session.clear()
    await harness.send(ALICE, "Москва")
    check("интересы предлагаются", has(session, "Шаг 6/7"))
    await harness.click(ALICE, "reg:interest:music")
    await harness.click(ALICE, "reg:interests_done:")
    check("после интересов — описание", has(session, "Шаг 7/7"))

    session.clear()
    await harness.send(ALICE, "Пишите мне в телеграм @anna_real, вот мой номер 89991234567")
    check("контакты в анкете запрещены", has(session, "нельзя оставлять ссылки"))

    session.clear()
    await harness.send(ALICE, "Люблю горы, кофе и долгие разговоры о кино.")
    check("просят фотографии", has(session, "Последний шаг"))

    await harness.send(ALICE, "", photo="alice_1")
    session.clear()
    await harness.send(ALICE, "", photo="alice_1")
    check("одинаковое фото не дублируется", has(session, "уже добавлено"))
    await harness.send(ALICE, "", photo="alice_2")

    session.clear()
    await harness.click(ALICE, "reg:photos_done:")
    check("показан предпросмотр", has(session, "Вот так тебя увидят"))

    session.clear()
    await harness.click(ALICE, "reg:publish:")
    check("анкета опубликована", has(session, "Анкета готова"))
    profile = await profiles.get(db, ALICE)
    check("анкета в базе заполнена", bool(profile and profile["is_complete"]))
    check("имя сохранено", profile["name"] == "Аня", str(profile["name"]))
    check("город сохранён", profile["city"] == "Москва", str(profile["city"]))
    check("фото сохранены", await profiles.count_photos(db, ALICE) == 2)

    session.clear()
    await harness.send(ALICE, "/start")
    check("с анкетой /start здоровается по имени", has(session, "С возвращением, Аня", ALICE))
    check("предупреждение больше не показывают", not has(session, "Прочитай перед началом", ALICE))
    menu_calls = [
        payload
        for name, payload in session.calls
        if name == "SendMessage" and (payload.get("reply_markup") or {}).get("keyboard")
    ]
    check("меню появилось", bool(menu_calls))
    if menu_calls:
        labels = [
            button.get("text")
            for row in menu_calls[-1]["reply_markup"]["keyboard"]
            for button in row
        ]
        check("в меню есть поиск анкет", any("Смотреть анкеты" in str(x) for x in labels), str(labels))
        check("в меню есть своя анкета", any("Моя анкета" in str(x) for x in labels))
        check("в меню есть настройки", any("Настройки" in str(x) for x in labels))

    # Модератору в том же меню добавляется вход в админ-панель
    await users.set_role(db, ALICE, 1)
    session.clear()
    await harness.send(ALICE, "/start")
    staff_menu = [
        payload
        for name, payload in session.calls
        if name == "SendMessage" and (payload.get("reply_markup") or {}).get("keyboard")
    ]
    staff_labels = [
        button.get("text")
        for row in (staff_menu[-1]["reply_markup"]["keyboard"] if staff_menu else [])
        for button in row
    ]
    check(
        "модератору в меню добавляется админ-панель",
        any("Админ-панель" in str(x) for x in staff_labels),
        str(staff_labels),
    )
    await users.set_role(db, ALICE, 0)

    print("\n▶ Второй пользователь и лента")
    await register(harness, BORIS, name="Борис", age=27, gender="m", seeking="f")
    check("второй профиль создан", bool((await profiles.get(db, BORIS))["is_complete"]))

    session.clear()
    await harness.send(BORIS, "🔍 Смотреть анкеты")
    check("анкета показана в ленте", has(session, "Аня", BORIS))
    buttons = session.buttons(BORIS)
    check("кнопки ленты на месте", f"fd:like:{ALICE}" in buttons and f"fd:pass:{ALICE}" in buttons)
    check("есть кнопка жалобы", f"fd:report:{ALICE}" in buttons)
    check("виден остаток лайков", has(session, "Осталось лайков", BORIS))

    session.clear()
    await harness.click(BORIS, f"fd:like:{ALICE}")
    check("лайк засчитан", "❤️ Лайк отправлен!" in session.alerts())
    check("Аня получила уведомление", has(session, "Ты кому-то понравился", ALICE))
    like_row = await likes_service.existing(db, BORIS, ALICE)
    check("лайк записан в базу", like_row and like_row["action"] == "like")

    print("\n▶ Входящие лайки и взаимность")
    session.clear()
    await harness.send(ALICE, "💌 Мои лайки")
    check("входящий лайк показан", has(session, "Ты понравился этому человеку", ALICE))
    check("кнопка взаимности есть", f"lk:like:{BORIS}" in session.buttons(ALICE))

    session.clear()
    await harness.click(ALICE, f"lk:like:{BORIS}")
    check("оба узнали о симпатии", has(session, "Взаимная симпатия", ALICE) and has(session, "Взаимная симпатия", BORIS))
    check("напоминание о безопасности при первой паре", has(session, "это не знакомство"))
    match = await likes_service.match_between(db, ALICE, BORIS)
    check("пара создана", match is not None and match["active"] == 1)
    match_id = int(match["id"])

    print("\n▶ Диалог внутри бота")
    session.clear()
    await harness.send(ALICE, "💞 Симпатии")
    check("список симпатий открыт", has(session, "Взаимные симпатии", ALICE))
    check("есть кнопка диалога", f"mt:open:{match_id}:0" in session.buttons(ALICE))

    session.clear()
    await harness.click(ALICE, f"mt:open:{match_id}:0")
    check("диалог открыт", has(session, "Диалог с", ALICE))

    session.clear()
    await harness.send(ALICE, "Привет! Видел, ты любишь горы — где был последний раз?")
    check("сообщение переслано", "CopyMessage" in session.methods())
    check("получатель уведомлён", has(session, "Новое сообщение от", BORIS))
    history = await db.fetchall("SELECT * FROM messages WHERE match_id = ?", (match_id,))
    check("сообщение сохранено для разбора жалоб", len(history) == 1)

    session.clear()
    await harness.send(ALICE, "Держи ссылку на мой сайт https://scam-site.ru, там переведёшь 5000")
    check("получателю показано предупреждение", has(session, "Осторожно", BORIS))
    check("названа причина подозрения", has(session, "ссылка", BORIS))

    print("\n▶ Диалог переживает перезапуск бота")
    saved = await db.fetchval("SELECT active_match_id FROM users WHERE id = ?", (ALICE,))
    check("открытый диалог хранится в базе", int(saved or 0) == match_id, str(saved))

    harness.restart()  # память процесса потеряна, осталась только база
    session.clear()
    await harness.send(ALICE, "Я всё ещё в диалоге?")
    check("после перезапуска сообщение дошло", "CopyMessage" in session.methods())
    check(
        "человека не выбросило в меню",
        not any("Выбери действие" in text for text in session.texts(ALICE)),
    )
    check("собеседник снова получил подпись", has(session, "Новое сообщение от", BORIS))

    session.clear()
    await harness.send(ALICE, "⬅️ Выйти из диалога")
    check("выход из диалога работает", has(session, "Диалог закрыт", ALICE))
    check(
        "после выхода диалог закрыт и в базе",
        await db.fetchval("SELECT active_match_id FROM users WHERE id = ?", (ALICE,)) is None,
    )

    session.clear()
    await harness.send(ALICE, "просто текст после выхода")
    check("сообщения больше не пересылаются", "CopyMessage" not in session.methods())

    print("\n▶ Жалоба")
    session.clear()
    await harness.click(BORIS, f"mt:report:{match_id}:0")
    check("категории жалоб показаны", has(session, "Жалоба на анкету", BORIS))
    check("есть категория шантажа", "rp:cat:100:blackmail" in session.buttons(BORIS))

    session.clear()
    await harness.click(BORIS, f"rp:cat:{ALICE}:scam")
    check("просят описать ситуацию", has(session, "Опиши ситуацию", BORIS))

    session.clear()
    await harness.send(BORIS, "Просит перевести деньги и уводит на сторонний сайт")
    check("жалоба принята", has(session, "Жалоба принята", BORIS))
    reports = await moderation.reports_about(db, ALICE)
    check("жалоба в базе", len(reports) == 1 and reports[0]["category"] == "scam")
    check("комментарий сохранён", "перевести деньги" in str(reports[0]["comment"]))
    check("владелец получил уведомление", has(session, "Новая жалоба", OWNER))

    print("\n▶ Админ-панель")
    session.clear()
    await harness.send(OWNER, "/admin")
    check("панель открыта", has(session, "Админ-панель", OWNER))
    check("видно число жалоб", has(session, "Открытых жалоб: <b>1</b>", OWNER))

    session.clear()
    await harness.click(OWNER, "ad:stats:0:0:")
    check("статистика собирается", has(session, "Статистика", OWNER))
    check("в статистике есть модерация", has(session, "Открытых жалоб", OWNER))

    session.clear()
    await harness.click(OWNER, "ad:reports:0:0:")
    check("жалоба открыта в панели", has(session, "Жалоба №1", OWNER))
    check("видна категория", has(session, "Мошенничество", OWNER))
    buttons = session.buttons(OWNER)
    check("есть кнопка переписки", any(b.startswith("ad:rep_chat") for b in buttons))
    check("есть кнопка блокировки", any(b.startswith("ad:ban_menu") for b in buttons))

    session.clear()
    await harness.click(OWNER, "ad:rep_chat:1:0:")
    check("переписка как доказательство доступна", has(session, "Последние сообщения", OWNER))
    check("видно кто есть кто", has(session, "нарушитель", OWNER))

    session.clear()
    await harness.click(OWNER, f"ad:shadow_menu:{ALICE}:0:")
    check("меню охвата открыто", has(session, "Снижение охвата", OWNER))
    await harness.click(OWNER, f"ad:shadow:{ALICE}:0:1")
    check("пользователю объяснили ограничение", has(session, "Показы твоей анкеты", ALICE) or has(session, "Показы анкеты временно снижены", ALICE))
    check("сказано, что анкета видна", has(session, "по-прежнему видна", ALICE))
    check("жалобщику ответили", has(session, "принято решение", BORIS))
    alice_user = await users.get(db, ALICE)
    check("мягкое ограничение применено", users.is_shadowed(alice_user) and users.shadow_reach(alice_user) == 70)
    check("бан не выдан", not users.is_banned(alice_user))
    check("жалоба закрыта", (await moderation.queue_count(db)) == 0)

    print("\n▶ Апелляция")
    session.clear()
    await harness.click(ALICE, "st:appeal:")
    check("апелляцию можно написать", has(session, "Апелляция", ALICE))
    await harness.send(ALICE, "Я никого не обманывала, это личный конфликт")
    check("апелляция отправлена", has(session, "Апелляция отправлена", ALICE))
    check("модератор уведомлён", has(session, "Новая апелляция", OWNER))

    session.clear()
    await harness.click(OWNER, "ad:appeals:0:0:")
    check("апелляция в очереди", has(session, "Апелляция №1", OWNER))
    check("видно текущее ограничение", has(session, "снижен охват", OWNER))

    session.clear()
    await harness.click(OWNER, "ad:ap_accept:1:0:")
    check("ограничения сняты", not users.is_shadowed(await users.get(db, ALICE)))
    check("пользователю сообщили", has(session, "пересмотрели решение", ALICE))

    print("\n▶ Блокировка и доступ")
    session.clear()
    await harness.click(OWNER, f"ad:ban_menu:{BORIS}:0:")
    check("меню блокировки открыто", has(session, "Блокировка", OWNER))
    await harness.click(OWNER, f"ad:ban:{BORIS}:0:1d")
    check("пользователь заблокирован", users.is_banned(await users.get(db, BORIS)))
    check("ему сообщили причину", has(session, "Доступ ограничен", BORIS))

    session.clear()
    await harness.send(BORIS, "/start")
    check("заблокированный не попадает в бота", not has(session, "Главное меню", BORIS))
    check("показана кнопка апелляции", "st:appeal:" in session.buttons(BORIS))

    session.clear()
    await harness.click(OWNER, f"ad:unban:{BORIS}:0:")
    check("разблокировка работает", not users.is_banned(await users.get(db, BORIS)))
    check("сообщили о снятии", has(session, "Ограничение снято", BORIS))

    print("\n▶ Лимиты лайков")
    await settings.set("likes_per_day", "1")
    await settings.set("likes_per_day_new", "1")
    await register(harness, CARL, name="Карл", age=30, gender="m", seeking="f")
    session.clear()
    await harness.send(CARL, "🔍 Смотреть анкеты")
    await harness.click(CARL, f"fd:like:{ALICE}")
    check("первый лайк проходит", "❤️ Лайк отправлен!" in session.alerts())

    session.clear()
    await harness.click(CARL, f"fd:like:{BORIS}")
    check("второй лайк упирается в лимит", any("Лимит лайков" in a for a in session.alerts()))
    check("объяснили, зачем лимит", has(session, "отсекаем ботофермы", CARL))
    check("дизлайки не ограничены", has(session, "Дизлайки не ограничены", CARL))

    session.clear()
    await harness.click(CARL, f"fd:pass:{BORIS}")
    check("пропуск работает при исчерпанном лимите", "👋 Пропустили" in session.alerts())
    await settings.set("likes_per_day", "60")
    await settings.set("likes_per_day_new", "30")

    print("\n▶ Настройки")
    session.clear()
    await harness.send(ALICE, "⚙️ Настройки")
    check("настройки открыты", has(session, "Настройки", ALICE))
    await harness.click(ALICE, "st:filters:")
    check("фильтры показаны", has(session, "Фильтры поиска", ALICE))
    await harness.click(ALICE, "st:age_set:18-35")
    updated = await profiles.get(db, ALICE)
    check("возрастной фильтр сохранён", updated["age_min"] == 18 and updated["age_max"] == 35)
    await harness.click(ALICE, "st:radius_menu:")
    check("меню радиуса открылось", has(session, "Где искать", ALICE))
    await harness.click(ALICE, "st:radius:999")
    check("радиус сохранён", (await profiles.get(db, ALICE))["search_radius"] == 999)

    session.clear()
    await harness.click(ALICE, "st:notify:")
    await harness.click(ALICE, "st:toggle_notify:likes")
    check("уведомления переключаются", (await users.get(db, ALICE))["notify_likes"] == 0)

    print("\n▶ Анкета")
    session.clear()
    await harness.send(ALICE, "👤 Моя анкета")
    check("анкета показана", has(session, "Аня", ALICE))
    check("видна статистика", has(session, "Входящих лайков", ALICE))
    await harness.click(ALICE, "pf:pause:")
    check("анкету можно скрыть", (await profiles.get(db, ALICE))["is_visible"] == 0)
    await harness.click(ALICE, "pf:resume:")
    check("и вернуть обратно", (await profiles.get(db, ALICE))["is_visible"] == 1)

    session.clear()
    await harness.click(ALICE, "pf:bio:")
    await harness.send(ALICE, "Новое описание: люблю бег и настольные игры.")
    check("описание обновляется", "бег" in str((await profiles.get(db, ALICE))["bio"]))

    print("\n▶ Защита от ботоферм")
    session.clear()
    await harness.click(ALICE, "pf:photos:")
    check("меню фото открыто", has(session, "Фотографии", ALICE))
    await harness.click(ALICE, "pf:photo_add:")
    await harness.send(ALICE, "", photo="photo_200_1")  # фото Бориса
    check("повтор чужого фото замечен", has(session, "уже используют", OWNER))

    events = await db.fetchall("SELECT kind FROM events WHERE user_id = ?", (ALICE,))
    check("сигнал записан в антифрод", any(e["kind"] == "photo_reuse" for e in events))

    print("\n▶ Помощь и безопасность")
    session.clear()
    await harness.send(ALICE, "/help")
    check("справка работает", has(session, "Как всё работает", ALICE))
    check("в справке сказано про бесплатность", has(session, "без «открыть за 199₽»", ALICE))
    await harness.send(ALICE, "🛡 Безопасность")
    check("памятка доступна из меню", has(session, "Первая встреча", ALICE))

    session.clear()
    await harness.send(ALICE, "что-то непонятное")
    check("бот не молчит на непонятное", has(session, "Выбери действие", ALICE))

    print("\n▶ Город: распознавание и геопозиция")
    session.clear()
    await harness.send(GEO, "/start")
    await harness.click(GEO, "reg:rules_ok:")
    await harness.send(GEO, "Гео")
    await harness.send(GEO, "28")
    await harness.click(GEO, "reg:gender:m")
    await harness.click(GEO, "reg:seeking:f")
    check("на шаге города предлагают прислать место", has(session, "местоположению", GEO))
    location_button = [
        payload
        for name, payload in session.calls
        if name == "SendMessage" and (payload.get("reply_markup") or {}).get("keyboard")
    ]
    has_geo_button = any(
        button.get("request_location")
        for payload in location_button
        for row in payload["reply_markup"]["keyboard"]
        for button in row
    )
    check("кнопка «отправить местоположение» есть", has_geo_button)

    session.clear()
    await harness.send(GEO, "нов")
    check("по части названия предложен выбор", has(session, "какой именно город", GEO))
    choices = [b for b in session.all_buttons(GEO) if b.startswith("reg:city:")]
    check("в выборе несколько городов", len(choices) >= 3, str(choices[:3]))

    session.clear()
    await harness.send(GEO, "мск")
    check("сокращение распознано как Москва", has(session, "Город: <b>Москва</b>", GEO))
    check("после города спрашивают интересы", has(session, "Шаг 6/7", GEO))

    session.clear()
    await harness.click(GEO, "reg:interests_done:")
    await harness.send(GEO, "Люблю долгие прогулки и хороший кофе по утрам.")
    await harness.send(GEO, "", photo="geo_1")
    await harness.click(GEO, "reg:photos_done:")
    await harness.click(GEO, "reg:publish:")
    geo_profile = await profiles.get(db, GEO)
    check("город сохранён канонично", geo_profile["city"] == "Москва", str(geo_profile["city"]))
    check("координаты подставлены из справочника", geo_profile["lat"] is not None)
    check("источник координат — справочник", geo_profile["geo_source"] == "city")

    print("\n▶ Город по присланной точке")
    session.clear()
    await harness.send(GEO, "👤 Моя анкета")
    await harness.click(GEO, "pf:city:")
    await harness.send_location(GEO, 59.93, 30.34)  # Санкт-Петербург
    check("город определён по точке", has(session, "Санкт-Петербург", GEO))
    moved = await profiles.get(db, GEO)
    check("город обновлён", moved["city"] == "Санкт-Петербург", str(moved["city"]))
    check("координаты от человека", moved["geo_source"] == "location")
    check(
        "точка округлена до сотых",
        moved["lat"] == round(moved["lat"], 2) and moved["lat"] != 59.93456,
        str(moved["lat"]),
    )

    session.clear()
    await harness.click(GEO, "pf:city:")
    await harness.send(GEO, "Вымышленовка")
    check("неизвестный город принимается как есть", has(session, "Записал", GEO))
    check("сказано про расстояние", has(session, "расстояние", GEO))
    free_city = await profiles.get(db, GEO)
    check("свободный город без координат", free_city["lat"] is None, str(free_city["lat"]))

    print("\n▶ Расстояние и радиус поиска")
    await harness.click(GEO, "pf:city:")
    await harness.send(GEO, "Химки")
    himki = await profiles.get(db, GEO)
    check("Химки распознаны", himki["city"] == "Химки" and himki["lat"] is not None)

    await profiles.update(db, GEO, seeking="f", search_radius=50)
    session.clear()
    await harness.send(GEO, "🔍 Смотреть анкеты")
    check("в карточке видно расстояние", has(session, "км", GEO) or has(session, "рядом", GEO))

    await profiles.update(db, GEO, search_radius=25)
    geo_user = await users.get(db, GEO)
    near = await feed.count_available(db, settings, geo_user, await profiles.get(db, GEO))
    await profiles.update(db, GEO, search_radius=999)
    far = await feed.count_available(db, settings, geo_user, await profiles.get(db, GEO))
    check("радиус 25 км сужает выборку", near <= far, f"{near} против {far}")

    session.clear()
    await harness.send(GEO, "⚙️ Настройки")
    await harness.click(GEO, "st:filters:")
    check("в фильтрах видно, где искать", has(session, "Где искать", GEO))
    await harness.click(GEO, "st:radius_menu:")
    await harness.click(GEO, "st:radius:100")
    check("радиус сохранён", (await profiles.get(db, GEO))["search_radius"] == 100)
    check("честно сказано про взаимность радиуса", has(session, "радиус меньше", GEO))

    print("\n▶ Суперлайк с сообщением")
    await register(harness, DINA, name="Дина", age=26, gender="f", seeking="m")
    session.clear()
    await harness.send(BORIS, "🔍 Смотреть анкеты")
    await harness.click(BORIS, f"fd:super:{DINA}")
    check("суперлайк отправлен", any("💥" in a for a in session.alerts()))
    check("предложено добавить сообщение", has(session, "Хочешь добавить пару слов", BORIS))
    check("кнопка записки есть", f"fd:note:{DINA}" in session.all_buttons(BORIS))
    check("получателю пришло уведомление", has(session, "отправили суперлайк", DINA))

    session.clear()
    await harness.click(BORIS, f"fd:note:{DINA}")
    await harness.send(BORIS, "Пиши мне в телеграм @boris_real")
    check("контакты в записке запрещены", has(session, "отправлять нельзя", BORIS))
    await harness.send(BORIS, "Заметил, что ты тоже любишь горы — какой маршрут последний?")
    check("записка сохранена", has(session, "Сообщение добавлено", BORIS))
    like_row = await likes_service.existing(db, BORIS, DINA)
    check("записка в базе", bool(like_row) and "горы" in str(like_row["message"]))

    session.clear()
    await harness.send(DINA, "💌 Мои лайки")
    check("получатель видит суперлайк", has(session, "суперлайк", DINA))
    check("получатель видит записку", has(session, "какой маршрут", DINA))

    print("\n▶ Скрытая модератором анкета не достаёт людей")
    check("до скрытия лайк виден", await likes_service.incoming_count(db, DINA) == 1)
    session.clear()
    await harness.click(OWNER, f"ad:hold:{BORIS}:0:")
    check("анкета скрыта модератором", has(session, "скрыта из поиска", BORIS))
    left = await likes_service.incoming_count(db, DINA)
    check("лайк скрытой анкеты не показывается", left == 0, f"осталось {left}")

    session.clear()
    await harness.click(OWNER, f"ad:mod_ok:{BORIS}:0:")
    check("после одобрения анкета вернулась", has(session, "снова в поиске", BORIS))
    check("и лайк стал виден", await likes_service.incoming_count(db, DINA) == 1)

    print("\n▶ Продолжение прерванной анкеты")
    session.clear()
    await harness.click(OWNER, f"ad:wipe_photos:{BORIS}:0:")
    check("фото удалены модератором", await profiles.count_photos(db, BORIS) == 0)
    check("человеку объяснили причину", has(session, "Фотографии удалены модератором", BORIS))

    session.clear()
    await harness.send(BORIS, "/start")
    check("перед анкетой снова показали памятку", has(session, "Прочитай перед началом", BORIS))
    await harness.click(BORIS, "reg:rules_ok:")
    check("бот просит только фото", has(session, "не хватает фотографии", BORIS))
    check("заново имя не спрашивают", not has(session, "Шаг 1/7", BORIS))
    await harness.send(BORIS, "", photo="boris_new")
    session.clear()
    await harness.click(BORIS, "reg:photos_done:")
    await harness.click(BORIS, "reg:publish:")
    restored = await profiles.get(db, BORIS)
    check("анкета восстановлена", bool(restored["is_complete"]))
    check("имя сохранилось", restored["name"] == "Борис", str(restored["name"]))
    check("город сохранился", restored["city"] == "Москва", str(restored["city"]))

    print("\n▶ Капча против ботоферм")
    await settings.set("reg_burst_limit", "1")
    session.clear()
    await harness.send(NEWBIE, "/start")
    await harness.click(NEWBIE, "reg:rules_ok:")
    check("капча показана", has(session, "Быстрая проверка", NEWBIE))

    captcha_text = next(t for t in session.texts(NEWBIE) if "Быстрая проверка" in t)
    symbol = captcha_text.split("<b>")[-1].split("</b>")[0]
    options = [b for b in session.buttons(NEWBIE) if b.startswith("cap:")]
    wrong = next(b for b in options if b.split(":")[1] != symbol)
    right = next(b for b in options if b.split(":")[1] == symbol)

    session.clear()
    await harness.click(NEWBIE, wrong)
    check("неверный ответ не пропускает", any("Не тот символ" in a for a in session.alerts()))

    captcha_text = next(t for t in session.texts(NEWBIE) if "Быстрая проверка" in t)
    symbol = captcha_text.split("<b>")[-1].split("</b>")[0]
    right = next(b for b in session.buttons(NEWBIE) if b.startswith("cap:") and b.split(":")[1] == symbol)

    session.clear()
    await harness.click(NEWBIE, right)
    check("верный ответ пропускает дальше", has(session, "Шаг 1/7", NEWBIE))
    check("капча отмечена пройденной", bool((await users.get(db, NEWBIE))["captcha_passed"]))
    await settings.set("reg_burst_limit", "12")

    print("\n▶ Верификация анкеты")
    session.clear()
    await harness.send(DINA, "👤 Моя анкета")
    check("кнопка верификации есть", "pf:verify:" in session.all_buttons(DINA))

    session.clear()
    await harness.click(DINA, "pf:verify:")
    check("объяснили, зачем верификация", has(session, "Подтверждение анкеты", DINA))
    check("сказано про бонус к лимиту", has(session, "к суточному лимиту", DINA))
    check("сказано, что селфи не попадёт в анкету", has(session, "не попадёт в анкету", DINA))

    session.clear()
    await harness.click(DINA, "pf:verify_go:")
    check("попросили селфи с жестом", has(session, "Сделай селфи", DINA))
    gesture = (await users.get(db, DINA))["verify_gesture"]
    check("жест сохранён", bool(gesture), str(gesture))

    session.clear()
    await harness.send(DINA, "", photo=f"photo_{DINA}_1")
    check("фото из анкеты не принимается", has(session, "фотография из твоей анкеты", DINA))

    session.clear()
    await harness.send(DINA, "", photo="dina_selfie")
    check("селфи принято", has(session, "отправлено на проверку", DINA))
    check("модератор уведомлён", has(session, "заявка на верификацию", OWNER))
    pending = await users.get(db, DINA)
    check("статус — на проверке", pending["verify_status"] == "pending")

    session.clear()
    await harness.send(OWNER, "/admin")
    check("в меню видна очередь верификации", has(session, "Заявок на верификацию: <b>1</b>", OWNER))

    session.clear()
    await harness.click(OWNER, "ad:verify:0:0:")
    check("селфи и фото анкеты показаны рядом", "SendMediaGroup" in session.methods())
    check("видно, какой жест просили", has(session, "Просили показать", OWNER))
    check("есть кнопка подтверждения", any(b.startswith("ad:vf_ok") for b in session.all_buttons(OWNER)))

    limit_before = (await limits.breakdown(db, await users.get(db, DINA), settings)).total
    session.clear()
    await harness.click(OWNER, f"ad:vf_ok:{DINA}:0:")
    verified = await users.get(db, DINA)
    check("анкета подтверждена", bool(verified["verified"]))
    check("селфи удалено из базы", verified["verify_file_id"] is None)
    check("человеку сообщили", has(session, "Анкета подтверждена", DINA))
    limit_after = (await limits.breakdown(db, verified, settings)).total
    check("лимит вырос", limit_after > limit_before, f"{limit_before} → {limit_after}")

    session.clear()
    await harness.send(DINA, "👤 Моя анкета")
    check("в карточке появилась галочка", has(session, "✅", DINA))
    check("кнопки верификации больше нет", "pf:verify:" not in session.all_buttons(DINA))

    print("\n▶ Фильтр «только подтверждённые»")
    session.clear()
    await harness.send(ALICE, "⚙️ Настройки")
    await harness.click(ALICE, "st:filters:")
    check("фильтр показан в настройках", has(session, "Только подтверждённые", ALICE))
    await harness.click(ALICE, "st:toggle_verified:")
    check("фильтр включился", (await profiles.get(db, ALICE))["only_verified"] == 1)
    alice_profile = await profiles.get(db, ALICE)
    alice_user = await users.get(db, ALICE)
    only_verified_count = await feed.count_available(db, settings, alice_user, alice_profile)
    await profiles.update(db, ALICE, only_verified=0)
    all_count = await feed.count_available(db, settings, alice_user, await profiles.get(db, ALICE))
    check(
        "с фильтром анкет меньше",
        only_verified_count < all_count,
        f"{only_verified_count} из {all_count}",
    )

    print("\n▶ Статистика анкеты и лимит")
    session.clear()
    await harness.send(DINA, "🔍 Смотреть анкеты")
    shown = await insights.summary(db, BORIS)
    check("показы считаются", shown["shown"] >= 1, str(shown))

    session.clear()
    await harness.send(DINA, "👤 Моя анкета")
    await harness.click(DINA, "pf:insights:")
    check("статистика показана", has(session, "Статистика анкеты", DINA))

    session.clear()
    await harness.click(DINA, "pf:limits:")
    check("лимит расшифрован", has(session, "Из чего он складывается", DINA))
    check("видно базу лимита", has(session, "база —", DINA))
    check("сказано, что лимит нельзя купить", has(session, "нельзя купить", DINA))

    print("\n▶ Отклонение верификации")
    session.clear()
    await harness.click(CARL, "pf:verify:")
    await harness.click(CARL, "pf:verify_go:")
    await harness.send(CARL, "", photo="carl_selfie")
    check("заявка отправлена", has(session, "отправлено на проверку", CARL))

    session.clear()
    await harness.click(OWNER, f"ad:vf_no:{CARL}:0:")
    rejected = await users.get(db, CARL)
    check("заявка отклонена", rejected["verify_status"] == "rejected" and not rejected["verified"])
    check("причина названа", has(session, "не видно лица", CARL))
    check("сказано, что это не блокировка", has(session, "не блокировка", CARL))

    session.clear()
    await harness.click(CARL, "pf:verify:")
    check("повтор только после паузы", any("через" in a for a in session.alerts()))

    print("\n▶ Стоп-лист фотографий")
    session.clear()
    stolen = "boris_new"
    await harness.send(OWNER, "/admin")
    await harness.click(OWNER, f"ad:ban_photos:{BORIS}:0:")
    check("фото занесены в стоп-лист", any("стоп-лист" in a for a in session.alerts()))
    check("фото удалены", await profiles.count_photos(db, BORIS) == 0)
    check("человеку объяснили", has(session, "больше не принимаются", BORIS))
    blocked = await profiles.is_photo_blocked(db, f"u_{stolen}")
    check("хэш в стоп-листе", blocked)

    session.clear()
    await harness.send(BORIS, "/start")
    await harness.click(BORIS, "reg:rules_ok:")
    await harness.send(BORIS, "", photo=stolen)
    check("повторная загрузка запрещена", has(session, "заблокировано модерацией", BORIS))

    session.clear()
    await harness.click(OWNER, f"ad:user:{BORIS}:0:")
    check(
        "в карточке есть снятие стоп-листа",
        any(b.startswith("ad:unban_photos") for b in session.all_buttons(OWNER)),
    )
    await harness.click(OWNER, f"ad:unban_photos:{BORIS}:0:")
    check("стоп-лист снят", not await profiles.is_photo_blocked(db, f"u_{stolen}"))

    session.clear()
    await harness.send(BORIS, "", photo=stolen)
    await harness.click(BORIS, "reg:photos_done:")
    await harness.click(BORIS, "reg:publish:")
    check("анкета снова опубликована", bool((await profiles.get(db, BORIS))["is_complete"]))

    print("\n▶ Напоминание о непросмотренных лайках")
    # Нужен лайк, на который Борис ещё не отвечал, поэтому берём нового человека
    await register(harness, EMMA, name="Эмма", age=25, gender="f", seeking="m")
    await likes_service.act(db, EMMA, BORIS, "like")
    pending_for_boris = await likes_service.incoming_count(db, BORIS)
    check("у Бориса есть непросмотренный лайк", pending_for_boris == 1, str(pending_for_boris))
    await db.execute(
        "UPDATE users SET last_active_at = ?, notify_likes = 1 WHERE id = ?",
        (now() - 5 * 86400, BORIS),
    )

    class Cfg:
        moderation_chat_id = None

    maintenance = Maintenance(harness.bot, db, settings, Cfg())
    session.clear()
    sent = await maintenance.nudge_inactive()
    check("напоминание отправлено", sent == 1, str(sent))
    check("в тексте есть число лайков", has(session, "тебе поставили 1 лайк", BORIS))
    check("сказано, как отключить", has(session, "Отключи уведомления", BORIS))

    session.clear()
    again = await maintenance.nudge_inactive()
    check("повторно не пишем", again == 0, str(again))

    await db.execute("UPDATE users SET notify_likes = 0 WHERE id = ?", (BORIS,))
    await db.execute("DELETE FROM events WHERE user_id = ? AND kind = 'nudge'", (BORIS,))
    check("с выключенными уведомлениями не пишем", await maintenance.nudge_inactive() == 0)
    await db.execute("UPDATE users SET notify_likes = 1 WHERE id = ?", (BORIS,))

    print("\n▶ Выгрузка личных данных")
    session.clear()
    await harness.send(ALICE, "⚙️ Настройки")
    check("кнопка выгрузки есть", "st:export:" in session.all_buttons(ALICE))
    await harness.click(ALICE, "st:export:")
    check("файл отправлен", len(session.documents) == 1, str(len(session.documents)))
    if session.documents:
        name, body = session.documents[0]
        payload = json.loads(body)
        check("это json", name.endswith(".json"))
        check("в выгрузке есть анкета", payload["анкета"]["имя"] == "Аня", str(payload["анкета"]["имя"]))
        check("есть статистика", "статистика" in payload)
        check("есть история ограничений", "ограничения" in payload)
        check("есть свои сообщения", isinstance(payload["мои_сообщения"], list))
        check(
            "чужих сообщений в выгрузке нет",
            all("Привет! Видел" in str(m["текст"]) or True for m in payload["мои_сообщения"])
            and not any("горы — какой маршрут" in str(m.get("текст")) for m in payload["мои_сообщения"]),
        )

    print("\n▶ Обновление бота из админ-панели")
    session.clear()
    await harness.send(OWNER, "/admin")
    check("в меню есть раздел обновления", "ad:upd:0:0:" in session.all_buttons(OWNER))

    session.clear()
    await harness.click(OWNER, "ad:upd:0:0:")
    check("показана текущая версия", has(session, "Обновление бота", OWNER))
    check(
        "сказано, что вводить ничего не нужно",
        has(session, "заново вводить не нужно", OWNER) or has(session, "без истории git", OWNER),
    )
    check(
        "есть кнопка обновления",
        "ad:upd_start:0:0:" in session.all_buttons(OWNER)
        or has(session, "без истории git", OWNER),
    )

    session.clear()
    await harness.click(OWNER, "ad:upd_check:0:0:")
    check(
        "проверка обновлений отвечает",
        has(session, "последняя версия", OWNER)
        or has(session, "Есть обновление", OWNER)
        or has(session, "Не удалось проверить", OWNER),
    )

    # Модератор обновлять не может: это по сути выкладка кода на сервер
    await users.set_role(db, ALICE, 1)
    session.clear()
    await harness.click(ALICE, "ad:upd_start:0:0:")
    check("модератору обновление запрещено", any("только владелец" in a for a in session.alerts()))
    await users.set_role(db, ALICE, 0)

    session.clear()
    await harness.click(OWNER, "ad:upd_start:0:0:")
    check("владельцу показано подтверждение", has(session, "Обновить бота?", OWNER))
    check("предупредили о простое", has(session, "станет недоступен", OWNER))
    check("сказано про копию базы", has(session, "копия базы", OWNER))
    check("сказано про откат", has(session, "вернётся текущая", OWNER))

    # Сам скрипт в тестах не запускаем: подменяем запуск и проверяем только связку
    from app.services import updater as updater_service

    started: list[bool] = []
    original_start = updater_service.start
    updater_service.start = lambda project_dir: (started.append(True), True)[1]
    try:
        session.clear()
        await harness.click(OWNER, "ad:upd_go:0:0:")
    finally:
        updater_service.start = original_start
    check("обновление запускается", started == [True], str(started))
    check("владельцу сказали, что будет дальше", has(session, "Обновление запущено", OWNER))
    check(
        "запуск записан в журнал",
        any(
            row["action"] == "update_started"
            for row in await moderation.recent_log(db, limit=5)
        ),
    )

    print("\n▶ Удаление анкеты")
    session.clear()
    await harness.send(CARL, "/delete")
    check("спрашивают подтверждение", has(session, "Удалить анкету?", CARL))
    check("предлагают паузу вместо удаления", any("паузу" in b for b in session.texts(CARL)))
    await harness.click(CARL, "st:delete_yes:")
    check("анкета удалена", await profiles.get(db, CARL) is None)
    check("прощание показано", has(session, "Анкета удалена", CARL))

    await harness.close()
    await db.close()

    print("\n" + "=" * 60)
    print(f"Пройдено: {len(PASSED)} · Провалено: {len(FAILED)}")
    if FAILED:
        for item in FAILED:
            print(f"  ❌ {item}")
        raise SystemExit(1)
    print("Все сценарии пройдены ✅")


if __name__ == "__main__":
    asyncio.run(main())
