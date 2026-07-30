from __future__ import annotations

import hmac
import os
import re
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

# Loads variables from a local .env file (if present) into the process
# environment before any os.environ.get() calls below read them.
load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = BASE_DIR / "data" / "requests.db"

DEFAULT_ADMIN_USERNAME = "demo_admin"
DEFAULT_ADMIN_PASSWORD = "ChangeMe-LocalDemo-9f3!"

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_REQUEST_TIMEOUT_SECONDS = 5

SERVICES: dict[str, dict[str, Any]] = {
    "landing": {
        "label": "Одностраничный сайт-визитка",
        "price": 30_000,
    },
    "store": {
        "label": "Интернет-магазин",
        "price": 80_000,
    },
    "ai_agent": {
        "label": "ИИ-агент для переписки",
        "price": 65_000,
    },
}

EXTRA_OPTIONS: dict[str, dict[str, Any]] = {
    "crm": {
        "label": "Интеграция с CRM",
        "price": 20_000,
    },
    "messengers": {
        "label": "Интеграция с Telegram/WhatsApp",
        "price": 25_000,
    },
    "payments": {
        "label": "Подключение онлайн-оплаты",
        "price": 15_000,
    },
    "urgent": {
        "label": "Срочная разработка",
        "price": 30_000,
    },
}

SERVICE_ALIASES = {
    "business_card": "landing",
    "business-card": "landing",
    "website": "landing",
    "shop": "store",
    "online_store": "store",
    "online-store": "store",
    "ai-agent": "ai_agent",
    "agent": "ai_agent",
    **{item["label"]: key for key, item in SERVICES.items()},
}

OPTION_ALIASES = {
    "telegram": "messengers",
    "telegram_whatsapp": "messengers",
    "telegram-whatsapp": "messengers",
    "online_payment": "payments",
    "online-payment": "payments",
    "payment": "payments",
    "rush": "urgent",
    **{item["label"]: key for key, item in EXTRA_OPTIONS.items()},
}

EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)
PHONE_PATTERN = re.compile(r"^\+?[0-9()\-\s]+$")

app = Flask(__name__)
app.config.from_mapping(
    DATABASE=os.environ.get("DATABASE_PATH", str(DEFAULT_DATABASE_PATH)),
    MAX_CONTENT_LENGTH=32 * 1024,
)


def get_db() -> sqlite3.Connection:
    """Return one SQLite connection per request/application context."""
    if "db" not in g:
        database_path = Path(current_app.config["DATABASE"]).expanduser()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(database_path, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create the application database and requests table when absent."""
    database_path = Path(current_app.config["DATABASE"]).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT NOT NULL DEFAULT '',
            service TEXT NOT NULL,
            options TEXT NOT NULL DEFAULT '',
            total_price INTEGER NOT NULL CHECK (total_price >= 0),
            comment TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    db.commit()


app.teardown_appcontext(close_db)


def empty_form_data() -> dict[str, Any]:
    return {
        "name": "",
        "phone": "",
        "email": "",
        "comment": "",
        "service": "landing",
        "options": [],
    }


def index_context(
    *,
    errors: dict[str, str] | None = None,
    form_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "services": SERVICES,
        "extra_options": EXTRA_OPTIONS,
        "errors": errors or {},
        "form_data": form_data or empty_form_data(),
    }


def normalize_service(raw_service: str) -> str:
    candidate = raw_service.strip()
    return SERVICE_ALIASES.get(candidate, candidate)


def normalize_options(raw_options: list[str]) -> tuple[list[str], bool]:
    normalized: list[str] = []
    has_unknown_option = False

    for raw_option in raw_options:
        candidate = raw_option.strip()
        option = OPTION_ALIASES.get(candidate, candidate)
        if option not in EXTRA_OPTIONS:
            has_unknown_option = True
            continue
        if option not in normalized:
            normalized.append(option)

    # Store and display options in the stable order used by the calculator.
    normalized.sort(key=list(EXTRA_OPTIONS).index)
    return normalized, has_unknown_option


def validate_submission() -> tuple[dict[str, Any], dict[str, str]]:
    raw_options = request.form.getlist("options")
    raw_options += request.form.getlist("options[]")

    service = normalize_service(request.form.get("service", ""))
    options, has_unknown_option = normalize_options(raw_options)

    data: dict[str, Any] = {
        "name": request.form.get("name", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "comment": request.form.get("comment", "").strip(),
        "service": service,
        "options": options,
    }
    errors: dict[str, str] = {}

    if not data["name"]:
        errors["name"] = "Укажите имя."
    elif len(data["name"]) < 2:
        errors["name"] = "Имя должно содержать не менее 2 символов."
    elif len(data["name"]) > 100:
        errors["name"] = "Имя должно содержать не более 100 символов."

    if not data["phone"]:
        errors["phone"] = "Укажите телефон."
    elif len(data["phone"]) > 40:
        errors["phone"] = "Телефон должен содержать не более 40 символов."
    elif not PHONE_PATTERN.fullmatch(data["phone"]):
        errors["phone"] = "Введите телефон, используя цифры, пробелы, скобки, «+» или «-»."
    else:
        phone_digits = re.sub(r"\D", "", data["phone"])
        if not 7 <= len(phone_digits) <= 15:
            errors["phone"] = "Телефон должен содержать от 7 до 15 цифр."

    if len(data["email"]) > 254:
        errors["email"] = "Email должен содержать не более 254 символов."
    elif data["email"] and not EMAIL_PATTERN.fullmatch(data["email"]):
        errors["email"] = "Введите корректный email."

    if len(data["comment"]) > 2_000:
        errors["comment"] = "Комментарий должен содержать не более 2000 символов."

    if service not in SERVICES:
        errors["service"] = "Выберите услугу из списка."

    if has_unknown_option:
        errors["options"] = "Выбрана неизвестная дополнительная опция."

    return data, errors


def calculate_total(service: str, options: list[str]) -> int:
    """Calculate the trusted server-side price from catalog identifiers."""
    service_price = int(SERVICES[service]["price"])
    options_price = sum(int(EXTRA_OPTIONS[option]["price"]) for option in options)
    return service_price + options_price


def notify_owner_telegram(
    *,
    name: str,
    phone: str,
    service_label: str,
    total_price: int,
    comment: str,
) -> None:
    """Best-effort Telegram notification for the site owner.

    Must never raise: a saved request is already committed to the database,
    and a Telegram outage or missing configuration must not affect the
    response returned to the visitor.
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return

    message = "\n".join(
        [
            "Новая заявка с сайта service-site",
            "",
            f"Имя: {name}",
            f"Телефон: {phone}",
            f"Услуга: {service_label}",
            f"Стоимость: {format_price(total_price)} ₽",
            f"Комментарий: {comment}",
        ]
    )

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    # Optional outbound proxy for hosts where api.telegram.org is not
    # directly reachable, e.g. "socks5h://127.0.0.1:1080". Unset by default.
    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "").strip()
    proxies = {"https": proxy_url} if proxy_url else None

    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message},
            proxies=proxies,
            timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        current_app.logger.warning("Telegram notification failed: %s", exc)
    except Exception:  # noqa: BLE001 - notification must never break submit()
        current_app.logger.exception("Unexpected error while notifying Telegram")


F = TypeVar("F", bound=Callable[..., Any])


def admin_auth_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Response | str:
        auth = request.authorization
        expected_username = (
            os.environ.get("ADMIN_USERNAME") or DEFAULT_ADMIN_USERNAME
        )
        expected_password = (
            os.environ.get("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD
        )

        supplied_username = auth.username if auth and auth.username else ""
        supplied_password = auth.password if auth and auth.password else ""
        username_matches = hmac.compare_digest(
            supplied_username.encode("utf-8"),
            expected_username.encode("utf-8"),
        )
        password_matches = hmac.compare_digest(
            supplied_password.encode("utf-8"),
            expected_password.encode("utf-8"),
        )
        is_basic_auth = bool(auth) and auth.type.casefold() == "basic"
        authenticated = is_basic_auth and username_matches and password_matches

        if not authenticated:
            return Response(
                "Требуется авторизация.",
                401,
                {"WWW-Authenticate": 'Basic realm="Admin", charset="UTF-8"'},
            )
        return view(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


@app.template_filter("format_price")
def format_price(value: int | str) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


@app.get("/")
def index() -> str:
    return render_template("index.html", **index_context())


@app.post("/submit")
def submit() -> tuple[str, int] | Response:
    data, errors = validate_submission()

    if errors:
        return (
            render_template(
                "index.html",
                **index_context(errors=errors, form_data=data),
            ),
            400,
        )

    total_price = calculate_total(data["service"], data["options"])
    service_label = str(SERVICES[data["service"]]["label"])
    option_labels = ", ".join(
        str(EXTRA_OPTIONS[option]["label"]) for option in data["options"]
    )
    created_at = datetime.now().astimezone().isoformat(
        sep=" ", timespec="seconds"
    )

    db = get_db()
    db.execute(
        """
        INSERT INTO requests (
            name,
            phone,
            email,
            service,
            options,
            total_price,
            comment,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["name"],
            data["phone"],
            data["email"],
            service_label,
            option_labels,
            total_price,
            data["comment"],
            created_at,
        ),
    )
    db.commit()

    notify_owner_telegram(
        name=data["name"],
        phone=data["phone"],
        service_label=service_label,
        total_price=total_price,
        comment=data["comment"],
    )

    return redirect(url_for("thanks"), code=303)


@app.get("/thanks")
def thanks() -> str:
    return render_template("thanks.html")


@app.get("/admin")
@admin_auth_required
def admin() -> str:
    saved_requests = get_db().execute(
        """
        SELECT
            id,
            name,
            phone,
            email,
            service,
            options,
            total_price,
            comment,
            created_at
        FROM requests
        ORDER BY id DESC
        """
    ).fetchall()
    return render_template(
        "admin.html",
        requests=saved_requests,
        requests_list=saved_requests,
    )


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run()
