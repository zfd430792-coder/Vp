"""Сквозные сценарии: имитируем нажатия и сообщения реального пользователя.

Запуск: python tests/test_bot.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config
from app.db import Database
from app.services import likes as likes_service
from app.services import moderation, profiles, users
from app.services.settings import Settings
from tests.harness import Harness

PASSED: list[str] = []
FAILED: list[str] = []

ALICE = 100
BORIS = 200
CARL = 300
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
    config = Config(token="1:x", owner_ids=(OWNER,), db_path=tmp, support_contact="@help")
    await users.sync_owners(db, (OWNER,))
    harness = Harness(db, settings, config)
    session = harness.session

    print("\n▶ Первый запуск и правила")
    session.clear()
    await harness.send(ALICE, "/start")
    check("приветствие показано", has(session, "Это бот знакомств", ALICE))
    check("правила показаны", has(session, "Правила", ALICE))
    check("есть кнопка безопасности", "reg:safety:" in session.buttons(ALICE))

    session.clear()
    await harness.click(ALICE, "reg:safety:")
    check("памятка о мошенниках открывается", has(session, "Признаки мошенника"))
    check("есть предупреждение о шантаже", has(session, "шантаж"))
    check("сказано не платить шантажисту", has(session, "Не плати"))

    session.clear()
    await harness.click(ALICE, "reg:rules_back:")
    await harness.click(ALICE, "reg:rules_no:")
    check("отказ от правил обрабатывается", has(session, "Без согласия с правилами"))
    stored = await users.get(db, ALICE)
    check("правила не приняты при отказе", not stored.get("rules_accepted_at"))

    print("\n▶ Регистрация")
    session.clear()
    await harness.send(ALICE, "/start")
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

    session.clear()
    await harness.send(ALICE, "⬅️ Выйти из диалога")
    check("выход из диалога работает", has(session, "Диалог закрыт", ALICE))

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
    await harness.click(ALICE, "st:toggle_city:")
    check("фильтр города переключается", (await profiles.get(db, ALICE))["only_my_city"] == 0)

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
