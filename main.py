import asyncio
import json
import os

import aiohttp
import trimesh
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv


load_dotenv(override=True)


def clean_secret(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


TOKEN = clean_secret(os.getenv("BOT_TOKEN"))
DADATA_TOKEN = clean_secret(os.getenv("DADATA_TOKEN"))
MANAGER_CHAT_ID = clean_secret(os.getenv("MANAGER_CHAT_ID"))
PRIVATE_ORDERS_SHEET_ID = clean_secret(os.getenv("PRIVATE_ORDERS_SHEET_ID"))
COMPANY_ORDERS_SHEET_ID = clean_secret(os.getenv("COMPANY_ORDERS_SHEET_ID"))
TELEGRAM_API_BASE_URL = clean_secret(os.getenv("TELEGRAM_API_BASE_URL"))
TELEGRAM_API_LOCAL_MODE = clean_secret(
    os.getenv("TELEGRAM_API_LOCAL_MODE") or os.getenv("TELEGRAM_API_IS_LOCAL")
).lower() in ("1", "true", "yes", "да")
DADATA_FIND_PARTY_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
STATE_FILE = "bot_state.json"
TELEGRAM_BOT_API_DOWNLOAD_LIMIT_MB = 20

if not TOKEN:
    raise RuntimeError("Заполните BOT_TOKEN в файле .env")


def create_bot() -> Bot:
    if not TELEGRAM_API_BASE_URL:
        return Bot(token=TOKEN)

    api_server = TelegramAPIServer.from_base(
        TELEGRAM_API_BASE_URL,
        is_local=TELEGRAM_API_LOCAL_MODE,
    )
    session = AiohttpSession(api=api_server)
    return Bot(token=TOKEN, session=session)


bot = create_bot()
dp = Dispatcher()

PRICE_PER_CM3 = 850
MIN_ORDER_PRICE = 400
USER_REGISTRATIONS = {}
USER_LAST_CALCULATIONS = {}
USER_CARTS = {}
USER_DELIVERIES = {}
USER_ORDER_NUMBERS = {}
USER_PENDING_FILES = {}
ORDER_COUNTER = {"next": 1}
HELP_BUTTON_TEXT = "/help"
CART_COMMAND = "cart"
CART_BUTTON_TEXT = "🛒 Корзина"
ADD_MORE_COMMAND = "add_more"
ADD_MORE_BUTTON_TEXT = "➕ Добавить еще"
SEND_ORDER_COMMAND = "send_order"
SEND_ORDER_TEXT = "📨 Отправить заказ менеджеру"
CALCULATE_BUTTON_TEXT = "🧮 Сделать расчет"
CHOOSE_DELIVERY_TEXT = "🚚 Выбрать доставку"
CHANGE_CART_MATERIAL_TEXT = "🔄 Изменить материал"
REMOVE_CART_ITEM_TEXT = "🗑 Удалить объект"
OTHER_DELIVERY_TEXT = "Другое (800 рублей)"
PICKUP_ADDRESS = "Москва, улица Академика Арцимовича, 13"
PICKUP_TEXT = f"Самовывоз — {PICKUP_ADDRESS}"
DELIVERY_PROMPT = (
    "Для оформления доставки и оплаты заказа, выберите адрес доставки или введите Ваш "
    "по кнопке ниже (800 рублей в пределах мкада)\n\n"
    f"Для оформления самовывоза по адресу \"{PICKUP_ADDRESS}\" нажмите соответствующую кнопку ниже"
)
HELP_TEXT = """📘 Краткая инструкция

/start — начать регистрацию заново.
/help — показать эту справку.
/cart — открыть корзину. Также можно нажать кнопку «🛒 Корзина».
/add_more — добавить еще STL-файлы к заказу.
/send_order — отправить оформленный заказ менеджеру.

Как пользоваться ботом:
1. Нажмите /start и выберите тип клиента.
2. Для частного лица отправьте контакт или введите данные вручную.
3. Для юрлица введите ИНН, подтвердите компанию и введите банковские реквизиты.
4. После регистрации загружайте STL-файлы по одному.
5. После каждого файла выберите материал финального отлива.
6. Когда все файлы загружены и материалы выбраны, нажмите «🧮 Сделать расчет».
7. Бот посчитает каждую модель и добавит рассчитанную пачку в корзину.

Расчет:
• базовая цена — 850 руб./см3;
• если объем больше 5 см3 — 650 руб./см3;
• минимальная стоимость общего заказа — 400 руб.

В корзине можно посмотреть итог до доставки, изменить материал финального отлива или удалить отдельную модель.
После формирования корзины можно нажать «➕ Добавить еще» и загрузить дополнительные файлы.
После расчета выберите адрес доставки: известные адреса бесплатные, другой адрес — 800 руб. в пределах МКАД.
Поддерживаемый файл: .stl
Большие STL-файлы поддерживаются при подключенном локальном Telegram Bot API сервере."""


class Registration(StatesGroup):
    choosing_customer_type = State()
    waiting_private_contact = State()
    waiting_private_name = State()
    waiting_private_phone = State()
    waiting_private_email = State()
    waiting_company_inn = State()
    confirming_company = State()
    waiting_company_bank_details = State()
    confirming_company_bank_details = State()


class MaterialSelection(StatesGroup):
    waiting_category = State()
    waiting_subcategory = State()
    confirming_material = State()


class CartManagement(StatesGroup):
    waiting_remove_index = State()
    waiting_material_item_index = State()
    waiting_material_category = State()
    waiting_material_subcategory = State()
    confirming_material_change = State()


class DeliverySelection(StatesGroup):
    waiting_custom_address = State()


MATERIAL_OPTIONS = {
    "🥈 Серебро": [
        "925 проба",
        "925 проба без цинка",
    ],
    "🥇 Золото": [
        "585 проба белая",
        "585 проба желтая",
        "585 проба красная",
        "750 проба белая",
        "750 проба желтая",
        "750 проба красная",
    ],
    "⚪ Платина": [
        "950 проба",
    ],
}

DELIVERY_OPTIONS = [
    {
        "text": "Москва, Хорошёвское шоссе, 16, стр. 3",
        "button_text": "Москва, Хорошёвское шоссе, 16, стр. 3 (беспл.)",
        "price": 0,
    },
    {
        "text": "Москва, проспект Мира, 95, стр. 1",
        "button_text": "Москва, проспект Мира, 95, стр. 1 (беспл.)",
        "price": 0,
    },
    {
        "text": "Москва, Скаковая улица, 36",
        "button_text": "Москва, Скаковая улица, 36 (беспл.)",
        "price": 0,
    },
]

PRIVATE_ORDERS_SHEET_COLUMNS = [
    "Номер заказа",
    "Telegram ID",
    "Имя",
    "Телефон",
    "Email",
    "Количество предметов",
    "Адрес доставки",
    "Цена заказа",
]

COMPANY_ORDERS_SHEET_COLUMNS = [
    "Номер заказа",
    "Telegram ID",
    "Имя",
    "Телефон",
    "Email",
    "Количество предметов",
    "Адрес доставки",
    "Цена заказа",
    "Компания",
    "ИНН",
    "Реквизиты компании",
]


def service_keyboard_row() -> list[KeyboardButton]:
    return [
        KeyboardButton(text=HELP_BUTTON_TEXT),
        KeyboardButton(text=CART_BUTTON_TEXT),
    ]


customer_type_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="👤 Частное лицо"),
            KeyboardButton(text="🏢 Юридическое лицо"),
        ],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

contact_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📱 Отправить контакт", request_contact=True),
        ],
        [
            KeyboardButton(text="✍️ Ввести вручную"),
        ],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="✅ Верно"),
            KeyboardButton(text="❌ Другая"),
        ],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

material_category_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=category)]
        for category in MATERIAL_OPTIONS
    ] + [service_keyboard_row()],
    resize_keyboard=True,
)


def build_material_subcategory_keyboard(category: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=subcategory)]
            for subcategory in MATERIAL_OPTIONS[category]
        ] + [service_keyboard_row()],
        resize_keyboard=True,
    )


material_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="✅ Верно"),
            KeyboardButton(text="🔄 Изменить"),
        ],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

bank_details_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="✅ Верно"),
            KeyboardButton(text="✏️ Исправить"),
        ],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

help_keyboard = ReplyKeyboardMarkup(
    keyboard=[service_keyboard_row()],
    resize_keyboard=True,
)

cart_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=SEND_ORDER_TEXT)],
        [
            KeyboardButton(text=ADD_MORE_BUTTON_TEXT),
            KeyboardButton(text=CHOOSE_DELIVERY_TEXT),
        ],
        [
            KeyboardButton(text=CHANGE_CART_MATERIAL_TEXT),
            KeyboardButton(text=REMOVE_CART_ITEM_TEXT),
        ],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

upload_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=CALCULATE_BUTTON_TEXT)],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)

delivery_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=option["button_text"])]
        for option in DELIVERY_OPTIONS
    ] + [
        [KeyboardButton(text=PICKUP_TEXT)],
        [KeyboardButton(text=OTHER_DELIVERY_TEXT)],
        service_keyboard_row(),
    ],
    resize_keyboard=True,
)


def normalize_text(text: str | None) -> str:
    return (text or "").strip()


def normalize_phone(text: str | None) -> str:
    return "".join(ch for ch in normalize_text(text) if ch.isdigit() or ch == "+")


def normalize_inn(text: str | None) -> str:
    return "".join(ch for ch in normalize_text(text) if ch.isdigit())


def is_valid_email(text: str | None) -> bool:
    email = normalize_text(text)
    return "@" in email and "." in email.split("@")[-1]


def is_valid_inn(inn: str) -> bool:
    return inn.isdigit() and len(inn) in (10, 12)


async def find_company_by_inn(inn: str) -> dict | None:
    if not DADATA_TOKEN:
        return {
            "inn": inn,
            "details": "Чтобы искать компанию или ИП по ИНН, задайте переменную окружения DADATA_TOKEN.",
            "lookup_configured": False,
            "raw": None,
        }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {DADATA_TOKEN}",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            DADATA_FIND_PARTY_URL,
            headers=headers,
            json={"query": inn},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status in (401, 403):
                raise RuntimeError(
                    "DaData не приняла токен. Проверьте, что в .env в DADATA_TOKEN указан именно API-ключ, "
                    "а не секретный ключ, и что аккаунт DaData подтвержден."
                )
            response.raise_for_status()
            payload = await response.json()

    suggestions = payload.get("suggestions", [])
    if not suggestions:
        return None

    suggestion = suggestions[0]
    data = suggestion.get("data") or {}
    address = data.get("address") or {}
    management = data.get("management") or {}

    return {
        "name": suggestion.get("value") or data.get("name", {}).get("full_with_opf") or "Без названия",
        "inn": data.get("inn") or inn,
        "kpp": data.get("kpp"),
        "ogrn": data.get("ogrn"),
        "address": address.get("value"),
        "manager": management.get("name"),
        "lookup_configured": True,
        "raw": suggestion,
    }


def format_company_info(company: dict) -> str:
    if not company.get("lookup_configured", True):
        return "\n".join(
            [
                f"🧾 ИНН: {company.get('inn')}",
                company["details"],
            ]
        )

    return f"🏢 {company.get('name')}"


def limit_text(text: str, limit: int = 3000) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n\n..."


def load_state():
    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Не удалось загрузить {STATE_FILE}: {error}")
        return

    USER_REGISTRATIONS.update(
        {int(user_id): registration for user_id, registration in state.get("registrations", {}).items()}
    )
    USER_CARTS.update(
        {int(user_id): cart for user_id, cart in state.get("carts", {}).items()}
    )
    USER_DELIVERIES.update(
        {int(user_id): delivery for user_id, delivery in state.get("deliveries", {}).items()}
    )
    USER_ORDER_NUMBERS.update(
        {int(user_id): order_number for user_id, order_number in state.get("order_numbers", {}).items()}
    )
    max_order_number = max(USER_ORDER_NUMBERS.values(), default=0)
    ORDER_COUNTER["next"] = max(int(state.get("next_order_number", 1)), max_order_number + 1)


def save_state():
    state = {
        "registrations": {str(user_id): registration for user_id, registration in USER_REGISTRATIONS.items()},
        "carts": {str(user_id): cart for user_id, cart in USER_CARTS.items()},
        "deliveries": {str(user_id): delivery for user_id, delivery in USER_DELIVERIES.items()},
        "order_numbers": {str(user_id): order_number for user_id, order_number in USER_ORDER_NUMBERS.items()},
        "next_order_number": ORDER_COUNTER["next"],
    }
    temp_file = f"{STATE_FILE}.tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False, indent=2)
        os.replace(temp_file, STATE_FILE)
    except OSError as error:
        print(f"Не удалось сохранить {STATE_FILE}: {error}")


def issue_order_number() -> int:
    order_number = ORDER_COUNTER["next"]
    ORDER_COUNTER["next"] += 1
    return order_number


def ensure_user_order_number(user_id: int) -> int:
    if user_id not in USER_ORDER_NUMBERS:
        USER_ORDER_NUMBERS[user_id] = issue_order_number()
    return USER_ORDER_NUMBERS[user_id]


def format_order_number(user_id: int) -> str:
    order_number = USER_ORDER_NUMBERS.get(user_id)
    if not order_number:
        return "не присвоен"
    return f"№{order_number:06d}"


def get_user_cart(user_id: int) -> list[dict]:
    return USER_CARTS.setdefault(user_id, [])


def get_user_pending_files(user_id: int) -> list[dict]:
    return USER_PENDING_FILES.setdefault(user_id, [])


def is_using_public_telegram_api() -> bool:
    return not TELEGRAM_API_BASE_URL


def is_too_large_for_public_telegram_api(file_size: int | None) -> bool:
    return bool(
        file_size
        and is_using_public_telegram_api()
        and file_size > TELEGRAM_BOT_API_DOWNLOAD_LIMIT_MB * 1024 * 1024
    )


def format_file_size(file_size: int | None) -> str:
    if not file_size:
        return "неизвестный размер"
    return f"{file_size / 1024 / 1024:.1f} МБ"


def large_file_setup_message(file_size: int | None = None) -> str:
    size_text = f" ({format_file_size(file_size)})" if file_size else ""
    return (
        f"📎 Файл больше {TELEGRAM_BOT_API_DOWNLOAD_LIMIT_MB} МБ{size_text}.\n\n"
        "Чтобы бот мог считать такие STL, на облаке нужно подключить локальный Telegram Bot API сервер "
        "и указать TELEGRAM_API_BASE_URL в .env. После этого большие файлы можно будет загружать так же, "
        "как обычные."
    )


def get_delivery_option(text: str | None) -> dict | None:
    normalized_text = normalize_text(text)
    for option in DELIVERY_OPTIONS:
        if option["text"] == normalized_text or option["button_text"] == normalized_text:
            return option
    return None


def format_money(value: float) -> str:
    return f"{value:.0f} руб."


def calculate_model_price(volume_cm3: float) -> tuple[float, int]:
    price_per_cm3 = PRICE_PER_CM3
    if volume_cm3 > 5:
        price_per_cm3 = 650

    price = volume_cm3 * price_per_cm3
    return price, price_per_cm3


def calculate_order_total(items: list[dict]) -> float:
    total = sum(item["price"] for item in items)
    if total and total < MIN_ORDER_PRICE:
        return MIN_ORDER_PRICE
    return total


def calculate_order_total_with_delivery(user_id: int) -> float:
    return calculate_order_total(get_user_cart(user_id)) + USER_DELIVERIES.get(user_id, {}).get("price", 0)


def should_show_item_prices(items: list[dict]) -> bool:
    return len(items) > 1


def safe_file_name(file_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in file_name)


def format_material(category: str | None, subcategory: str | None) -> str:
    if category and subcategory:
        return f"{category}, {subcategory}"
    return "не выбран"


def format_delivery(user_id: int) -> str:
    delivery = USER_DELIVERIES.get(user_id)
    if not delivery:
        return "не выбран"

    price = delivery.get("price", 0)
    price_text = "бесплатно" if price == 0 else format_money(price)
    return f"{delivery.get('address')} ({price_text})"


def format_registration_summary(registration: dict) -> str:
    customer_type = registration.get("customer_type")
    if customer_type == "private":
        return "\n".join(
            [
                "Тип клиента: физическое лицо",
                f"Имя: {registration.get('name', 'не указано')}",
                f"Телефон: {registration.get('phone', 'не указан')}",
                f"Email: {registration.get('email', 'не указан')}",
            ]
        )

    if customer_type == "company":
        company = registration.get("company") or {}
        return "\n".join(
            [
                "Тип клиента: юридическое лицо",
                f"ИНН: {registration.get('inn', 'не указан')}",
                f"Компания: {company.get('name', 'не указана')}",
                f"Реквизиты: {limit_text(registration.get('bank_details', 'не указаны'), 800)}",
            ]
        )

    return "Тип клиента: не указан"


def format_cart(user_id: int) -> str:
    cart = get_user_cart(user_id)
    if not cart:
        return (
            "🛒 Корзина пустая.\n\n"
            "Загрузите .stl файл после регистрации, выберите материал — и модель появится здесь."
        )

    lines = ["🛒 Корзина"]
    if user_id in USER_ORDER_NUMBERS:
        lines.append(f"Заказ {format_order_number(user_id)}")
    lines.append("")
    show_item_prices = should_show_item_prices(cart)
    for index, item in enumerate(cart, start=1):
        lines.extend(
            [
                f"{index}. {item['file_name']}",
                f"   Объем: {item['volume_cm3']:.3f} см3",
                f"   Материал: {format_material(item.get('material_category'), item.get('material_subcategory'))}",
            ]
        )
        if show_item_prices:
            lines.append(f"   Цена: {format_money(item['price'])}")
        lines.append("")

    lines.append(f"Итого до доставки: {format_money(calculate_order_total(cart))}")
    lines.append(f"Доставка: {format_delivery(user_id)}")
    if user_id in USER_DELIVERIES:
        lines.append(f"Итого с доставкой: {format_money(calculate_order_total_with_delivery(user_id))}")
    return "\n".join(lines)


def format_manager_order_summary(user_id: int) -> str:
    registration = USER_REGISTRATIONS.get(user_id, {})
    lines = [
        "Сводка заказа для менеджера",
        "",
        f"Заказ: {format_order_number(user_id)}",
        f"Telegram ID: {user_id}",
        format_registration_summary(registration),
        f"Доставка: {format_delivery(user_id)}",
        "",
        format_cart(user_id),
    ]
    return "\n".join(lines)


def get_delivery_for_sheet(user_id: int) -> str:
    delivery = USER_DELIVERIES.get(user_id)
    if not delivery:
        return ""
    return format_delivery(user_id)


def build_private_order_sheet_row(user_id: int) -> list:
    registration = USER_REGISTRATIONS.get(user_id, {})
    return [
        format_order_number(user_id),
        user_id,
        registration.get("name", ""),
        registration.get("phone", ""),
        registration.get("email", ""),
        len(get_user_cart(user_id)),
        get_delivery_for_sheet(user_id),
        format_money(calculate_order_total_with_delivery(user_id)),
    ]


def build_company_order_sheet_row(user_id: int) -> list:
    registration = USER_REGISTRATIONS.get(user_id, {})
    company = registration.get("company") or {}
    return [
        format_order_number(user_id),
        user_id,
        registration.get("name", ""),
        registration.get("phone", ""),
        registration.get("email", ""),
        len(get_user_cart(user_id)),
        get_delivery_for_sheet(user_id),
        format_money(calculate_order_total_with_delivery(user_id)),
        company.get("name", ""),
        registration.get("inn", ""),
        registration.get("bank_details", ""),
    ]


async def append_order_row_to_google_sheet(sheet_id: str, columns: list[str], row: list, table_name: str):
    if not sheet_id:
        print(f"Google Sheets заглушка: не указан ID таблицы для «{table_name}».")
        print(dict(zip(columns, row)))
        return

    # TODO: Подключить Google Sheets API и append строки в таблицу sheet_id.
    print(f"Google Sheets заглушка: готова строка для «{table_name}» ({sheet_id}).")
    print(dict(zip(columns, row)))


async def append_order_to_google_sheets(user_id: int):
    registration = USER_REGISTRATIONS.get(user_id, {})
    customer_type = registration.get("customer_type")

    if customer_type == "private":
        await append_order_row_to_google_sheet(
            PRIVATE_ORDERS_SHEET_ID,
            PRIVATE_ORDERS_SHEET_COLUMNS,
            build_private_order_sheet_row(user_id),
            "Частные лица",
        )
        return

    if customer_type == "company":
        await append_order_row_to_google_sheet(
            COMPANY_ORDERS_SHEET_ID,
            COMPANY_ORDERS_SHEET_COLUMNS,
            build_company_order_sheet_row(user_id),
            "Юридические лица",
        )
        return

    print(f"Google Sheets заглушка: неизвестный тип клиента для пользователя {user_id}.")


async def send_order_files_to_manager(user_id: int):
    cart = get_user_cart(user_id)
    order_number = format_order_number(user_id)
    missing_files = []

    for index, item in enumerate(cart, start=1):
        file_id = item.get("file_id")
        file_name = item.get("file_name", f"file_{index}.stl")
        if not file_id:
            missing_files.append(file_name)
            continue

        await bot.send_document(
            MANAGER_CHAT_ID,
            file_id,
            caption=f"{order_number} — файл {index}/{len(cart)}: {file_name}",
        )

    if missing_files:
        await bot.send_message(
            MANAGER_CHAT_ID,
            "Не удалось прикрепить файлы без сохраненного file_id:\n"
            + "\n".join(f"• {file_name}" for file_name in missing_files),
        )


def format_calculation_summary(items: list[dict], errors: list[str] | None = None) -> str:
    lines = ["🧮 Расчет готов", ""]
    show_item_prices = should_show_item_prices(items)
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"{index}. {item['file_name']}",
                f"   Объем: {item['volume_mm3']:.2f} мм3",
                f"   Объем: {item['volume_cm3']:.3f} см3",
                f"   Материал: {format_material(item.get('material_category'), item.get('material_subcategory'))}",
            ]
        )
        if show_item_prices:
            lines.append(f"   Цена: {format_money(item['price'])}")
        lines.append("")

    lines.append(f"Итого до доставки: {format_money(calculate_order_total(items))}")

    if errors:
        lines.extend(["", "Не удалось обработать:"])
        lines.extend(f"• {error}" for error in errors)

    return "\n".join(lines)


def format_pending_file_material_prompt(pending_files: list[dict], item_index: int) -> str:
    item = pending_files[item_index]
    return (
        f"💍 Материал для файла {item_index + 1}/{len(pending_files)}\n"
        f"{item['file_name']}\n\n"
        "Выберите материал финального отлива:"
    )


def find_pending_file_without_material(pending_files: list[dict]) -> int | None:
    for index, pending_file in enumerate(pending_files):
        if not pending_file.get("material_category") or not pending_file.get("material_subcategory"):
            return index
    return None


def parse_cart_index(text: str | None, cart: list[dict]) -> int | None:
    try:
        index = int(normalize_text(text))
    except ValueError:
        return None

    if 1 <= index <= len(cart):
        return index - 1
    return None


def is_cart_request(text: str | None) -> bool:
    return normalize_text(text).lower() in {
        "корзина",
        "/корзина",
        "/korzina",
        CART_BUTTON_TEXT.lower(),
    }


def is_add_more_request(text: str | None) -> bool:
    return normalize_text(text).lower() in {
        "добавить еще",
        "добавить ещё",
        "/dobavit",
        "/add",
        f"/{ADD_MORE_COMMAND}",
        ADD_MORE_BUTTON_TEXT.lower(),
    }


async def send_cart(message: Message):
    cart = get_user_cart(message.from_user.id)
    await message.answer(
        format_cart(message.from_user.id),
        reply_markup=cart_keyboard if cart else help_keyboard,
    )


async def ask_to_upload_more(message: Message, state: FSMContext):
    if message.from_user.id not in USER_REGISTRATIONS:
        await message.answer("📝 Сначала пройдите регистрацию через /start.", reply_markup=help_keyboard)
        return

    await state.clear()
    await message.answer(
        "📎 Загружайте новые .stl файлы по одному. После каждого файла выберите материал, затем нажмите «🧮 Сделать расчет».",
        reply_markup=upload_keyboard,
    )


async def ask_delivery_address(message: Message, state: FSMContext):
    await message.answer(
        DELIVERY_PROMPT,
        reply_markup=delivery_keyboard,
    )
    await state.set_state(DeliverySelection.waiting_custom_address)


async def save_delivery(message: Message, state: FSMContext, address: str, price: int, kind: str):
    if not get_user_cart(message.from_user.id):
        await message.answer("Сначала добавьте модели в корзину.", reply_markup=upload_keyboard)
        await state.clear()
        return

    USER_DELIVERIES[message.from_user.id] = {
        "address": address,
        "price": price,
        "kind": kind,
    }
    save_state()
    await state.clear()
    await message.answer(
        f"✅ Адрес доставки сохранен:\n{format_delivery(message.from_user.id)}\n\n{format_cart(message.from_user.id)}",
        reply_markup=cart_keyboard,
    )


async def send_order_to_manager(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not get_user_cart(user_id):
        await message.answer("Сначала добавьте модели в корзину.", reply_markup=upload_keyboard)
        return

    if user_id not in USER_DELIVERIES:
        await message.answer("Сначала выберите доставку или самовывоз.", reply_markup=delivery_keyboard)
        await state.set_state(DeliverySelection.waiting_custom_address)
        return

    if not MANAGER_CHAT_ID:
        await message.answer(
            "Не настроен MANAGER_CHAT_ID в .env, поэтому я пока не могу отправить заказ менеджеру.",
            reply_markup=cart_keyboard,
        )
        return

    had_order_number = user_id in USER_ORDER_NUMBERS
    if not had_order_number:
        USER_ORDER_NUMBERS[user_id] = ORDER_COUNTER["next"]

    summary = format_manager_order_summary(user_id)

    try:
        await bot.send_message(MANAGER_CHAT_ID, summary)
        await send_order_files_to_manager(user_id)
    except Exception as error:
        if not had_order_number:
            USER_ORDER_NUMBERS.pop(user_id, None)
        await message.answer(
            f"Не удалось отправить заказ менеджеру.\nОшибка: {error}",
            reply_markup=cart_keyboard,
        )
        return

    if not had_order_number:
        ORDER_COUNTER["next"] += 1
    save_state()
    await append_order_to_google_sheets(user_id)

    await state.clear()
    await message.answer(
        f"✅ Заказ {format_order_number(user_id)} отправлен менеджеру.",
        reply_markup=cart_keyboard,
    )


async def ask_material_for_pending_file(message: Message, state: FSMContext, item_index: int):
    pending_files = get_user_pending_files(message.from_user.id)
    if not pending_files or item_index >= len(pending_files):
        await message.answer(
            "Сначала загрузите .stl файл.",
            reply_markup=upload_keyboard,
        )
        await state.clear()
        return

    await state.update_data(
        pending_file_index=item_index,
        material_category=None,
        material_subcategory=None,
    )
    await message.answer(
        format_pending_file_material_prompt(pending_files, item_index),
        reply_markup=material_category_keyboard,
    )
    await state.set_state(MaterialSelection.waiting_category)


async def calculate_pending_files(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in USER_REGISTRATIONS:
        await message.answer("📝 Сначала пройдите регистрацию через /start.", reply_markup=help_keyboard)
        return

    pending_files = get_user_pending_files(user_id)
    if not pending_files:
        await message.answer(
            "📎 Сначала загрузите один или несколько .stl файлов.",
            reply_markup=upload_keyboard,
        )
        return

    file_without_material_index = find_pending_file_without_material(pending_files)
    if file_without_material_index is not None:
        await message.answer(
            "💍 Сначала выберем материал для файлов, где он еще не указан."
        )
        await ask_material_for_pending_file(message, state, file_without_material_index)
        return

    await state.clear()
    await message.answer(f"🧮 Начинаю расчет: {len(pending_files)} файл(ов).")

    os.makedirs("models", exist_ok=True)
    calculated_items = []
    errors = []

    for index, pending_file in enumerate(pending_files, start=1):
        file_name = pending_file["file_name"]
        file_path = os.path.join("models", f"{user_id}_{index}_{safe_file_name(file_name)}")
        await message.answer(f"Считаю {index}/{len(pending_files)}: {file_name}")

        try:
            if is_too_large_for_public_telegram_api(pending_file.get("file_size")):
                errors.append(f"{file_name}: нужен локальный Telegram Bot API сервер для файлов больше 20 МБ")
                continue

            await bot.download(pending_file["file_id"], destination=file_path)
            mesh = trimesh.load(file_path)

            volume_mm3 = mesh.volume
            volume_cm3 = volume_mm3 / 1000
            price, price_per_cm3 = calculate_model_price(volume_cm3)

            calculated_items.append(
                {
                    "file_name": file_name,
                    "file_id": pending_file.get("file_id"),
                    "volume_mm3": volume_mm3,
                    "volume_cm3": volume_cm3,
                    "price": price,
                    "price_per_cm3": price_per_cm3,
                    "material_category": pending_file.get("material_category"),
                    "material_subcategory": pending_file.get("material_subcategory"),
                }
            )
        except Exception as error:
            errors.append(f"{file_name}: {error}")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    USER_PENDING_FILES[user_id] = []

    if not calculated_items:
        await message.answer(
            f"⚠️ Не удалось рассчитать файлы.\n\n" + "\n".join(errors),
            reply_markup=upload_keyboard,
        )
        return

    USER_LAST_CALCULATIONS[user_id] = calculated_items
    get_user_cart(user_id).extend(item.copy() for item in calculated_items)
    save_state()
    await message.answer(
        f"{format_calculation_summary(calculated_items, errors)}\n\n✅ Добавлено в корзину.\n\n{DELIVERY_PROMPT}",
        reply_markup=delivery_keyboard,
    )
    await state.set_state(DeliverySelection.waiting_custom_address)


async def ask_to_confirm_bank_details(message: Message, state: FSMContext, bank_details: str):
    await state.update_data(bank_details=bank_details)
    await message.answer(
        f"🔎 Я распознал реквизиты:\n\n{limit_text(bank_details)}\n\nВсе верно?",
        reply_markup=bank_details_confirm_keyboard,
    )
    await state.set_state(Registration.confirming_company_bank_details)


async def complete_registration(message: Message, state: FSMContext, registration_data: dict):
    USER_REGISTRATIONS[message.from_user.id] = registration_data
    save_state()
    print(registration_data)

    await message.answer(
        "✅ Регистрация завершена.\n📎 Загрузите один или несколько .stl файлов, затем нажмите «🧮 Сделать расчет».",
        reply_markup=upload_keyboard,
    )
    await state.clear()


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(HELP_TEXT, reply_markup=help_keyboard)


@dp.message(Command(CART_COMMAND))
async def cart_command(message: Message):
    await send_cart(message)


@dp.message(lambda message: is_cart_request(message.text))
async def cart_button(message: Message):
    await send_cart(message)


@dp.message(Command(ADD_MORE_COMMAND))
async def add_more_command(message: Message, state: FSMContext):
    await ask_to_upload_more(message, state)


@dp.message(lambda message: is_add_more_request(message.text))
async def add_more_button(message: Message, state: FSMContext):
    await ask_to_upload_more(message, state)


@dp.message(Command(SEND_ORDER_COMMAND))
async def send_order_command(message: Message, state: FSMContext):
    await send_order_to_manager(message, state)


@dp.message(F.text == SEND_ORDER_TEXT)
async def send_order_button(message: Message, state: FSMContext):
    await send_order_to_manager(message, state)


@dp.message(F.text == CHOOSE_DELIVERY_TEXT)
async def choose_delivery_from_cart(message: Message, state: FSMContext):
    if not get_user_cart(message.from_user.id):
        await message.answer("Сначала добавьте модели в корзину.", reply_markup=upload_keyboard)
        return

    await ask_delivery_address(message, state)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    USER_PENDING_FILES.pop(message.from_user.id, None)
    await state.clear()

    if message.from_user.id in USER_REGISTRATIONS:
        await message.answer(
            "✅ Вы уже зарегистрированы.\n📎 Загружайте .stl файлы по одному, после каждого выбирайте материал, затем нажмите «🧮 Сделать расчет».",
            reply_markup=upload_keyboard,
        )
        return

    await message.answer(
        "👋 Выберите тип клиента:",
        reply_markup=customer_type_keyboard,
    )
    await state.set_state(Registration.choosing_customer_type)


@dp.message(F.text.in_([option["button_text"] for option in DELIVERY_OPTIONS]))
async def choose_known_delivery_address(message: Message, state: FSMContext):
    option = get_delivery_option(message.text)
    if not option:
        await ask_delivery_address(message, state)
        return

    await save_delivery(
        message=message,
        state=state,
        address=option["text"],
        price=option["price"],
        kind="known",
    )


@dp.message(F.text == OTHER_DELIVERY_TEXT)
async def choose_custom_delivery_address(message: Message, state: FSMContext):
    await message.answer(
        "Введите адрес доставки в пределах МКАД:",
        reply_markup=help_keyboard,
    )
    await state.set_state(DeliverySelection.waiting_custom_address)


@dp.message(F.text == PICKUP_TEXT)
async def choose_pickup(message: Message, state: FSMContext):
    await save_delivery(
        message=message,
        state=state,
        address=PICKUP_ADDRESS,
        price=0,
        kind="pickup",
    )


@dp.message(DeliverySelection.waiting_custom_address)
async def get_custom_delivery_address(message: Message, state: FSMContext):
    address = normalize_text(message.text)
    if not address:
        await message.answer("Введите адрес доставки текстом:", reply_markup=help_keyboard)
        return

    await save_delivery(
        message=message,
        state=state,
        address=address,
        price=800,
        kind="custom",
    )


@dp.message(F.text == CALCULATE_BUTTON_TEXT)
async def calculate_button(message: Message, state: FSMContext):
    await calculate_pending_files(message, state)


@dp.message(F.text == CHANGE_CART_MATERIAL_TEXT)
async def start_cart_material_change(message: Message, state: FSMContext):
    cart = get_user_cart(message.from_user.id)
    if not cart:
        await send_cart(message)
        return

    await message.answer(
        f"{format_cart(message.from_user.id)}\n\nВведите номер модели, у которой нужно изменить материал:",
        reply_markup=help_keyboard,
    )
    await state.set_state(CartManagement.waiting_material_item_index)


@dp.message(F.text == REMOVE_CART_ITEM_TEXT)
async def start_cart_item_remove(message: Message, state: FSMContext):
    cart = get_user_cart(message.from_user.id)
    if not cart:
        await send_cart(message)
        return

    await message.answer(
        f"{format_cart(message.from_user.id)}\n\nВведите номер модели, которую нужно удалить:",
        reply_markup=help_keyboard,
    )
    await state.set_state(CartManagement.waiting_remove_index)


@dp.message(CartManagement.waiting_remove_index)
async def remove_cart_item(message: Message, state: FSMContext):
    cart = get_user_cart(message.from_user.id)
    cart_index = parse_cart_index(message.text, cart)
    if cart_index is None:
        await message.answer("Введите номер модели из корзины:", reply_markup=help_keyboard)
        return

    removed_item = cart.pop(cart_index)
    if not cart:
        USER_DELIVERIES.pop(message.from_user.id, None)
        USER_ORDER_NUMBERS.pop(message.from_user.id, None)
    save_state()
    await message.answer(
        f"🗑 Удалено: {removed_item['file_name']}\n\n{format_cart(message.from_user.id)}",
        reply_markup=cart_keyboard if cart else help_keyboard,
    )
    await state.clear()


@dp.message(CartManagement.waiting_material_item_index)
async def choose_cart_item_for_material_change(message: Message, state: FSMContext):
    cart = get_user_cart(message.from_user.id)
    cart_index = parse_cart_index(message.text, cart)
    if cart_index is None:
        await message.answer("Введите номер модели из корзины:", reply_markup=help_keyboard)
        return

    await state.update_data(cart_item_index=cart_index)
    await message.answer(
        "💍 Выберите новый материал финального отлива:",
        reply_markup=material_category_keyboard,
    )
    await state.set_state(CartManagement.waiting_material_category)


@dp.message(CartManagement.waiting_material_category, F.text.in_(MATERIAL_OPTIONS.keys()))
async def choose_cart_material_category(message: Message, state: FSMContext):
    category = message.text
    await state.update_data(material_category=category)
    await message.answer(
        "🔬 Выберите пробу:",
        reply_markup=build_material_subcategory_keyboard(category),
    )
    await state.set_state(CartManagement.waiting_material_subcategory)


@dp.message(CartManagement.waiting_material_category)
async def choose_cart_material_category_again(message: Message):
    await message.answer(
        "💍 Пожалуйста, выберите материал из списка:",
        reply_markup=material_category_keyboard,
    )


@dp.message(CartManagement.waiting_material_subcategory)
async def choose_cart_material_subcategory(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("material_category")
    subcategory = normalize_text(message.text)

    if not category:
        await message.answer(
            "💍 Пожалуйста, выберите материал:",
            reply_markup=material_category_keyboard,
        )
        await state.set_state(CartManagement.waiting_material_category)
        return

    if subcategory not in MATERIAL_OPTIONS.get(category, []):
        await message.answer(
            "🔬 Пожалуйста, выберите пробу из списка:",
            reply_markup=build_material_subcategory_keyboard(category),
        )
        return

    await state.update_data(material_subcategory=subcategory)
    await message.answer(
        f"💍 Новый материал:\n{category}\n{subcategory}\n\nПодтвердите изменение?",
        reply_markup=material_confirm_keyboard,
    )
    await state.set_state(CartManagement.confirming_material_change)


@dp.message(CartManagement.confirming_material_change, F.text == "✅ Верно")
async def confirm_cart_material_change(message: Message, state: FSMContext):
    data = await state.get_data()
    cart = get_user_cart(message.from_user.id)
    cart_index = data.get("cart_item_index")

    if cart_index is None or cart_index >= len(cart):
        await message.answer(
            "Позиция в корзине не найдена. Откройте корзину и попробуйте еще раз.",
            reply_markup=help_keyboard,
        )
        await state.clear()
        return

    cart[cart_index]["material_category"] = data.get("material_category")
    cart[cart_index]["material_subcategory"] = data.get("material_subcategory")
    save_state()

    await message.answer(
        f"✅ Материал обновлен.\n\n{format_cart(message.from_user.id)}",
        reply_markup=cart_keyboard,
    )
    await state.clear()


@dp.message(CartManagement.confirming_material_change, F.text == "🔄 Изменить")
async def retry_cart_material_change(message: Message, state: FSMContext):
    await state.update_data(material_category=None, material_subcategory=None)
    await message.answer(
        "💍 Выберите новый материал финального отлива:",
        reply_markup=material_category_keyboard,
    )
    await state.set_state(CartManagement.waiting_material_category)


@dp.message(CartManagement.confirming_material_change)
async def confirm_cart_material_change_again(message: Message):
    await message.answer(
        "👇 Нажмите «✅ Верно» или «🔄 Изменить».",
        reply_markup=material_confirm_keyboard,
    )


@dp.message(Registration.choosing_customer_type, F.text == "👤 Частное лицо")
async def choose_private_person(message: Message, state: FSMContext):
    await state.update_data(
        customer_type="private",
        telegram_id=message.from_user.id,
    )
    await message.answer(
        "📱 Отправьте контакт кнопкой ниже или выберите ручной ввод.",
        reply_markup=contact_keyboard,
    )
    await state.set_state(Registration.waiting_private_contact)


@dp.message(Registration.choosing_customer_type, F.text == "🏢 Юридическое лицо")
async def choose_company(message: Message, state: FSMContext):
    await state.update_data(
        customer_type="company",
        telegram_id=message.from_user.id,
    )
    await message.answer(
        "🧾 Введите ИНН ИП или компании:",
        reply_markup=help_keyboard,
    )
    await state.set_state(Registration.waiting_company_inn)


@dp.message(Registration.choosing_customer_type)
async def choose_customer_type_again(message: Message):
    await message.answer(
        "👇 Пожалуйста, выберите: частное лицо или юридическое лицо.",
        reply_markup=customer_type_keyboard,
    )


@dp.message(Registration.waiting_private_contact, F.contact)
async def get_private_contact(message: Message, state: FSMContext):
    contact = message.contact

    await state.update_data(
        name=contact.first_name,
        phone=contact.phone_number,
    )

    await message.answer(
        "📧 Введите email:",
        reply_markup=help_keyboard,
    )
    await state.set_state(Registration.waiting_private_email)


@dp.message(Registration.waiting_private_contact, F.text == "✍️ Ввести вручную")
async def enter_private_data_manually(message: Message, state: FSMContext):
    await message.answer(
        "👤 Введите имя:",
        reply_markup=help_keyboard,
    )
    await state.set_state(Registration.waiting_private_name)


@dp.message(Registration.waiting_private_contact)
async def ask_private_contact_again(message: Message):
    await message.answer(
        "👇 Нажмите кнопку отправки контакта или выберите ручной ввод.",
        reply_markup=contact_keyboard,
    )


@dp.message(Registration.waiting_private_name)
async def get_private_name(message: Message, state: FSMContext):
    name = normalize_text(message.text)
    if not name:
        await message.answer("👤 Введите имя текстом:")
        return

    await state.update_data(name=name)
    await message.answer("📞 Введите телефон:")
    await state.set_state(Registration.waiting_private_phone)


@dp.message(Registration.waiting_private_phone)
async def get_private_phone(message: Message, state: FSMContext):
    phone = normalize_phone(message.text)
    if len(phone) < 7:
        await message.answer("📞 Введите корректный телефон:")
        return

    await state.update_data(phone=phone)
    await message.answer("📧 Введите email:")
    await state.set_state(Registration.waiting_private_email)


@dp.message(Registration.waiting_private_email)
async def get_private_email(message: Message, state: FSMContext):
    email = normalize_text(message.text)
    if not is_valid_email(email):
        await message.answer("📧 Введите корректный email:")
        return

    await state.update_data(email=email)
    data = await state.get_data()
    await complete_registration(message, state, data)


@dp.message(Registration.waiting_company_inn)
async def get_company_inn(message: Message, state: FSMContext):
    inn = normalize_inn(message.text)
    if not is_valid_inn(inn):
        await message.answer("🧾 ИНН должен состоять из 10 или 12 цифр. Введите ИНН еще раз:")
        return

    try:
        company = await find_company_by_inn(inn)
    except Exception as error:
        await message.answer(
            "⚠️ Не удалось выполнить поиск по ИНН.\n"
            f"Ошибка: {error}\n\n"
            "🧾 Проверьте ИНН и введите его еще раз:"
        )
        return

    if not company:
        await message.answer("🔎 По этому ИНН ничего не найдено. Введите ИНН еще раз:")
        return

    await state.update_data(inn=inn, company=company)
    await message.answer(
        f"{format_company_info(company)}\n\nПодтвердите компанию?",
        reply_markup=confirm_keyboard,
    )
    await state.set_state(Registration.confirming_company)


@dp.message(Registration.confirming_company, F.text == "✅ Верно")
async def confirm_company(message: Message, state: FSMContext):
    await message.answer(
        "🏦 Введите банковские реквизиты текстом:",
        reply_markup=help_keyboard,
    )
    await state.set_state(Registration.waiting_company_bank_details)


@dp.message(Registration.confirming_company, F.text == "❌ Другая")
async def retry_company_inn(message: Message, state: FSMContext):
    await state.update_data(inn=None, company=None)
    await message.answer(
        "🧾 Введите ИНН еще раз:",
        reply_markup=help_keyboard,
    )
    await state.set_state(Registration.waiting_company_inn)


@dp.message(Registration.confirming_company)
async def confirm_company_again(message: Message):
    await message.answer(
        "👇 Нажмите «✅ Верно» или «❌ Другая».",
        reply_markup=confirm_keyboard,
    )


@dp.message(Registration.waiting_company_bank_details)
async def get_company_bank_details(message: Message, state: FSMContext):
    bank_details_text = normalize_text(message.text)

    if not bank_details_text:
        await message.answer("🏦 Введите банковские реквизиты текстом:")
        return

    await state.update_data(bank_details_type="text")
    await ask_to_confirm_bank_details(message, state, bank_details_text)


@dp.message(Registration.confirming_company_bank_details, F.text == "✅ Верно")
async def confirm_company_bank_details(message: Message, state: FSMContext):
    data = await state.get_data()
    await complete_registration(message, state, data)


@dp.message(Registration.confirming_company_bank_details, F.text == "✏️ Исправить")
async def edit_company_bank_details(message: Message, state: FSMContext):
    await message.answer(
        "✏️ Введите банковские реквизиты текстом:",
        reply_markup=help_keyboard,
    )
    await state.set_state(Registration.waiting_company_bank_details)


@dp.message(Registration.confirming_company_bank_details)
async def confirm_company_bank_details_again(message: Message):
    await message.answer(
        "👇 Нажмите «✅ Верно» или «✏️ Исправить».",
        reply_markup=bank_details_confirm_keyboard,
    )


@dp.message(F.document)
async def process_stl(message: Message, state: FSMContext):
    if message.from_user.id not in USER_REGISTRATIONS:
        await message.answer("📝 Сначала пройдите регистрацию через /start.", reply_markup=help_keyboard)
        return

    document = message.document

    if not document.file_name.lower().endswith(".stl"):
        await message.answer("📎 Пожалуйста, отправьте .stl файл.", reply_markup=upload_keyboard)
        return

    if is_too_large_for_public_telegram_api(document.file_size):
        await message.answer(large_file_setup_message(document.file_size), reply_markup=upload_keyboard)
        return

    await state.clear()
    pending_files = get_user_pending_files(message.from_user.id)
    pending_files.append(
        {
            "file_id": document.file_id,
            "file_name": document.file_name,
            "file_size": document.file_size,
            "material_category": None,
            "material_subcategory": None,
        }
    )
    await message.answer(f"📎 Файл добавлен: {document.file_name}")
    await ask_material_for_pending_file(message, state, len(pending_files) - 1)


@dp.message(MaterialSelection.waiting_category, F.text.in_(MATERIAL_OPTIONS.keys()))
async def choose_material_category(message: Message, state: FSMContext):
    category = message.text
    await state.update_data(material_category=category)
    await message.answer(
        "🔬 Выберите пробу:",
        reply_markup=build_material_subcategory_keyboard(category),
    )
    await state.set_state(MaterialSelection.waiting_subcategory)


@dp.message(MaterialSelection.waiting_category)
async def choose_material_category_again(message: Message):
    await message.answer(
        "💍 Пожалуйста, выберите одну из категорий материала:",
        reply_markup=material_category_keyboard,
    )


@dp.message(MaterialSelection.waiting_subcategory)
async def choose_material_subcategory(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("material_category")
    subcategory = normalize_text(message.text)

    if not category:
        await message.answer(
            "💍 Пожалуйста, выберите категорию материала:",
            reply_markup=material_category_keyboard,
        )
        await state.set_state(MaterialSelection.waiting_category)
        return

    if subcategory not in MATERIAL_OPTIONS.get(category, []):
        await message.answer(
            "🔬 Пожалуйста, выберите пробу из списка:",
            reply_markup=build_material_subcategory_keyboard(category),
        )
        return

    await state.update_data(material_subcategory=subcategory)
    await message.answer(
        f"💍 Вы выбрали:\n{category}\n{subcategory}\n\nПодтвердите материал?",
        reply_markup=material_confirm_keyboard,
    )
    await state.set_state(MaterialSelection.confirming_material)


@dp.message(MaterialSelection.confirming_material, F.text == "✅ Верно")
async def confirm_material(message: Message, state: FSMContext):
    data = await state.get_data()
    category = data.get("material_category")
    subcategory = data.get("material_subcategory")
    pending_file_index = data.get("pending_file_index")

    pending_files = get_user_pending_files(message.from_user.id)
    if pending_file_index is None or pending_file_index >= len(pending_files):
        await message.answer("Сначала загрузите .stl файл.", reply_markup=upload_keyboard)
        await state.clear()
        return

    pending_files[pending_file_index]["material_category"] = category
    pending_files[pending_file_index]["material_subcategory"] = subcategory

    next_file_without_material_index = find_pending_file_without_material(pending_files)
    if next_file_without_material_index is not None:
        await message.answer(
            "✅ Материал сохранен для файла:\n"
            f"{pending_files[pending_file_index]['file_name']}\n"
            f"{format_material(category, subcategory)}"
        )
        await ask_material_for_pending_file(message, state, next_file_without_material_index)
        return

    await message.answer(
        "✅ Материал сохранен для файла:\n"
        f"{pending_files[pending_file_index]['file_name']}\n"
        f"{format_material(category, subcategory)}\n\n"
        "Загрузите следующий .stl файл или нажмите «🧮 Сделать расчет».",
        reply_markup=upload_keyboard,
    )
    await state.clear()


@dp.message(MaterialSelection.confirming_material, F.text == "🔄 Изменить")
async def change_material(message: Message, state: FSMContext):
    await state.update_data(material_category=None, material_subcategory=None)
    await message.answer(
        "💍 Выберите категорию материала:",
        reply_markup=material_category_keyboard,
    )
    await state.set_state(MaterialSelection.waiting_category)


@dp.message(MaterialSelection.confirming_material)
async def confirm_material_again(message: Message):
    await message.answer(
        "👇 Нажмите «✅ Верно» или «🔄 Изменить».",
        reply_markup=material_confirm_keyboard,
    )


async def main():
    load_state()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать регистрацию"),
            BotCommand(command="help", description="Краткая инструкция"),
            BotCommand(command=CART_COMMAND, description="Открыть корзину"),
            BotCommand(command=ADD_MORE_COMMAND, description="Добавить еще файлы"),
            BotCommand(command=SEND_ORDER_COMMAND, description="Отправить заказ менеджеру"),
        ]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
