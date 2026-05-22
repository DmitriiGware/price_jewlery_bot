import asyncio
import os

import aiohttp
import trimesh
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
USER_REGISTRATIONS = {}
USER_LAST_CALCULATIONS = {}


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


MATERIAL_OPTIONS = {
    "🥈 Серебро": [
        "925 проба",
        "925 проба без цинка",
    ],
    "🥇 Золото": [
        "585 проба белая",
        "750 проба белая",
        "585 проба желтая",
        "750 проба желтая",
        "585 проба красная",
        "750 проба красная",
    ],
    "⚪ Платина": [
        "950 проба",
    ],
}


customer_type_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="👤 Частное лицо"),
            KeyboardButton(text="🏢 Юридическое лицо"),
        ]
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
    ],
    resize_keyboard=True,
)

confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="✅ Верно"),
            KeyboardButton(text="❌ Другая"),
        ]
    ],
    resize_keyboard=True,
)

material_category_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=category)]
        for category in MATERIAL_OPTIONS
    ],
    resize_keyboard=True,
)


def build_material_subcategory_keyboard(category: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=subcategory)]
            for subcategory in MATERIAL_OPTIONS[category]
        ],
        resize_keyboard=True,
    )


material_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="✅ Верно"),
            KeyboardButton(text="🔄 Изменить"),
        ]
    ],
    resize_keyboard=True,
)

bank_details_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="✅ Верно"),
            KeyboardButton(text="✏️ Исправить"),
        ]
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
        "✅ Регистрация завершена.\n📎 Загрузите ваш .stl файл для расчета.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.clear()


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Выберите тип клиента:",
        reply_markup=customer_type_keyboard,
    )
    await state.set_state(Registration.choosing_customer_type)


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
        reply_markup=ReplyKeyboardRemove(),
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
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Registration.waiting_private_email)


@dp.message(Registration.waiting_private_contact, F.text == "✍️ Ввести вручную")
async def enter_private_data_manually(message: Message, state: FSMContext):
    await message.answer(
        "👤 Введите имя:",
        reply_markup=ReplyKeyboardRemove(),
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
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Registration.waiting_company_bank_details)


@dp.message(Registration.confirming_company, F.text == "❌ Другая")
async def retry_company_inn(message: Message, state: FSMContext):
    await state.update_data(inn=None, company=None)
    await message.answer(
        "🧾 Введите ИНН еще раз:",
        reply_markup=ReplyKeyboardRemove(),
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
        reply_markup=ReplyKeyboardRemove(),
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
    await state.clear()

    if message.from_user.id not in USER_REGISTRATIONS:
        await message.answer("📝 Сначала пройдите регистрацию через /start.")
        return

    document = message.document

    if not document.file_name.lower().endswith(".stl"):
        await message.answer("📎 Пожалуйста, отправьте .stl файл.")
        return

    os.makedirs("models", exist_ok=True)
    file_path = os.path.join("models", document.file_name)

    await bot.download(document, destination=file_path)

    try:
        mesh = trimesh.load(file_path)

        volume_mm3 = mesh.volume
        volume_cm3 = volume_mm3 / 1000

        price_per_cm3 = PRICE_PER_CM3
        if volume_cm3 > 5:
            price_per_cm3 = 650

        price = volume_cm3 * price_per_cm3
        if price < 400:
            price = 400

        text = (
            f"📐 Объем: {volume_mm3:.2f} мм3\n"
            f"📦 Объем: {volume_cm3:.3f} см3\n"
            f"💰 Стоимость: {price:.0f} руб."
        )

        await message.answer(text)
        USER_LAST_CALCULATIONS[message.from_user.id] = {
            "file_name": document.file_name,
            "volume_mm3": volume_mm3,
            "volume_cm3": volume_cm3,
            "price": price,
            "price_per_cm3": price_per_cm3,
        }
        await message.answer(
            "💍 Выберите категорию материала:",
            reply_markup=material_category_keyboard,
        )
        await state.set_state(MaterialSelection.waiting_category)

    except Exception as error:
        await message.answer(f"⚠️ Ошибка обработки:\n{error}")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


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

    last_calculation = USER_LAST_CALCULATIONS.get(message.from_user.id, {})
    last_calculation["material_category"] = category
    last_calculation["material_subcategory"] = subcategory
    USER_LAST_CALCULATIONS[message.from_user.id] = last_calculation

    await message.answer(
        f"✅ Материал выбран:\n{category}\n{subcategory}",
        reply_markup=ReplyKeyboardRemove(),
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
