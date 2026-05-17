# Telegram-бот «Мои документы»

AI-консультант для пользователей РФ: консультация, подбор документа и генерация DOCX/PDF по юридическим шаблонам. Опционально ЮKassa (redirect + локальный webhook).

## Запуск локально

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Заполните `.env`: `BOT_TOKEN`, `DEEPSEEK_API_KEYS`, `DATABASE_URL`, при нужде `REDIS_URL`, ЮKassa и webhook (см. `.env.example`).

```powershell
alembic upgrade head
python -m app.main
```

## Деплой на VPS (отдельно от других проектов)

Принцип: свой каталог, свой виртуальный env, свой systemd‑юнит и отдельный `location` в nginx — без правок чужих сервисов.

### 1) Клон и окружение (пример пути)

```bash
sudo mkdir -p /srv/TGBOT-i_saidru
sudo chown "$USER:$USER" /srv/TGBOT-i_saidru
cd /srv/TGBOT-i_saidru
git clone https://github.com/SiteCraftorCPP/TGBOT-i_saidru.git .
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env
chmod 600 .env
nano .env   # секреты только здесь, в git не попадают
```

### 2) База и миграции

PostgreSQL рекомендуется в продакшене: создайте **отдельную** БД и пользователя только для этого бота, пропишите `DATABASE_URL` в `.env`. Для SQLite оставьте URL из примера (файл `*.db` в каталоге проекта не коммитится).

```bash
source .venv/bin/activate
alembic upgrade head
```

### 3) ЮKassa webhook (не пересекается с другими сайтами)

Бот слушает HTTP только для webhook (порт задаётся `YOOKASSA_WEBHOOK_PORT`, путь — `YOOKASSA_WEBHOOK_PATH`). В личном кабинете ЮKassa URL должен совпасть с тем, что проксирует nginx.

Пример фрагмента в **уже существующем** `server { ... }` вашего домена (уникальный путь только для этого бота):

```nginx
location /yookassa/webhook {
    proxy_pass http://127.0.0.1:8890;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

После правки:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 4) systemd (работа только из каталога проекта)

Файл `/etc/systemd/system/tgbot-isaidru.service` (или другое уникальное имя):

```ini
[Unit]
Description=Telegram bot TGBOT-i_saidru (ЮрДок)
After=network.target

[Service]
Type=simple
User=ВАШ_ПОЛЬЗОВАТЕЛЬ
Group=ВАША_ГРУППА
WorkingDirectory=/srv/TGBOT-i_saidru
EnvironmentFile=/srv/TGBOT-i_saidru/.env
ExecStart=/srv/TGBOT-i_saidru/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Замените пользователя и путь при необходимости.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tgbot-isaidru.service
journalctl -u tgbot-isaidru -f
```

На сервер должны быть установлены Python 3.11+ и, для PDF, `libreoffice` (совпадает с `LIBREOFFICE_PATH` / `soffice` в `PATH`).

## Windows и PDF

Для PDF нужен LibreOffice. Если `soffice` не найден в `PATH`, укажите полный путь:

```env
LIBREOFFICE_PATH=C:\Program Files\LibreOffice\program\soffice.exe
```

## Платежи / ЮKassa

При `PAYMENTS_ENABLED=true` нужны ключи ЮKassa, HTTPS `YOOKASSA_RETURN_URL`, положительный `YOOKASSA_WEBHOOK_PORT` и прокси на ваш `YOOKASSA_WEBHOOK_PATH` (см. `.env.example`). В админке учитываются только записи со статусом **`paid`** (после успешного webhook).

## Шаблоны

Документы собираются из `templates/**/metadata.json`; при необходимости DOCX лежит рядом с metadata (локально не коммитьте чужие бинарники без нужды — в `.gitignore` игнорируются `templates/**/*.docx`).

## Проверка

```powershell
python -m compileall app
pytest
```
