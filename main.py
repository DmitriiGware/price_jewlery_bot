import asyncio
import os

import aiohttp
import trimesh
from aiogram import Bot, Dispatcher, F
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
DADATA_FIND_PARTY_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"

if not TOKEN:
    raise RuntimeError("Заполните BOT_TOKEN в файле .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()

PRICE_PER_CM3 = 850
MIN_ORDER_PRICE = 400
USER_REGISTRATIONS = {}
USER_LAST_CALCULATIONS = {}
USER_CARTS = {}
USER_PENDING_FILES = {}
HELP_BUTTON_TEXT = "/help"
CART_COMMAND = "cart"
CART_BUTTON_TEXT = "🛒 Корзина"
CALCULATE_BUTTON_TEXT = "🧮 Сделать расчет"
CHANGE_CART_MATERIAL_TEXT = "🔄 Изменить материал"
REMOVE_CART_ITEM_TEXT = "🗑 Удалить объект"
HELP_TEXT = """📘 Краткая инструкция

/start — начать регистрацию заново.
/help — показать эту справку.
/cart — открыть корзину. Также можно нажать кнопку «🛒 Корзина».

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
Поддерживаемый файл: .stl"""


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


def get_user_cart(user_id: int) -> list[dict]:
    return USER_CARTS.setdefault(user_id, [])


def get_user_pending_files(user_id: int) -> list[dict]:
    return USER_PENDING_FILES.setdefault(user_id, [])


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


def safe_file_name(file_name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in file_name)


def format_material(category: str | None, subcategory: str | None) -> str:
    if category and subcategory:
        return f"{category}, {subcategory}"
    return "не выбран"


def format_cart(user_id: int) -> str:
    cart = get_user_cart(user_id)
    if not cart:
        return (
            "🛒 Корзина пустая.\n\n"
            "Загрузите .stl файл после регистрации, выберите материал — и модель появится здесь."
        )

    lines = ["🛒 Корзина", ""]
    for index, item in enumerate(cart, start=1):
        lines.extend(
            [
                f"{index}. {item['file_name']}",
                f"   Объем: {item['volume_cm3']:.3f} см3",
                f"   Материал: {format_material(item.get('material_category'), item.get('material_subcategory'))}",
                f"   Цена: {format_money(item['price'])}",
                "",
            ]
        )

    lines.append(f"Итого до доставки: {format_money(calculate_order_total(cart))}")
    return "\n".join(lines)


def format_calculation_summary(items: list[dict], errors: list[str] | None = None) -> str:
    lines = ["🧮 Расчет готов", ""]
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"{index}. {item['file_name']}",
                f"   Объем: {item['volume_mm3']:.2f} мм3",
                f"   Объем: {item['volume_cm3']:.3f} см3",
                f"   Материал: {format_material(item.get('material_category'), item.get('material_subcategory'))}",
                f"   Цена: {format_money(item['price'])}",
                "",
            ]
        )

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


async def send_cart(message: Message):
    cart = get_user_cart(message.from_user.id)
    await message.answer(
        format_cart(message.from_user.id),
        reply_markup=cart_keyboard if cart else help_keyboard,
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
            await bot.download(pending_file["file_id"], destination=file_path)
            mesh = trimesh.load(file_path)

            volume_mm3 = mesh.volume
            volume_cm3 = volume_mm3 / 1000
            price, price_per_cm3 = calculate_model_price(volume_cm3)

            calculated_items.append(
                {
                    "file_name": file_name,
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
    await message.answer(
        f"{format_calculation_summary(calculated_items, errors)}\n\n✅ Добавлено в корзину.",
        reply_markup=cart_keyboard,
    )


async def ask_to_confirm_bank_details(message: Message, state: FSMContext, bank_details: str):
    await state.update_data(bank_details=bank_details)
    await message.answer(
        f"🔎 Я распознал реквизиты:\n\n{limit_text(bank_details)}\n\nВсе верно?",
        reply_markup=bank_details_confirm_keyboard,
    )
    await state.set_state(Registration.confirming_company_bank_details)


async def complete_registration(message: Message, state: FSMContext, registration_data: dict):
    USER_REGISTRATIONS[message.from_user.id] = registration_data
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


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    USER_PENDING_FILES.pop(message.from_user.id, None)
    await state.clear()
    await message.answer(
        "👋 Выберите тип клиента:",
        reply_markup=customer_type_keyboard,
    )
    await state.set_state(Registration.choosing_customer_type)


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

    await state.clear()
    pending_files = get_user_pending_files(message.from_user.id)
    pending_files.append(
        {
            "file_id": document.file_id,
            "file_name": document.file_name,
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
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Начать регистрацию"),
            BotCommand(command="help", description="Краткая инструкция"),
            BotCommand(command=CART_COMMAND, description="Открыть корзину"),
        ]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
