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

### 1) Клон и виртуальное окружение

Вы уже в каталоге (пример `/srv/TGBOT-i_saidru`). Дальше:

```bash
cd /srv/TGBOT-i_saidru
git clone https://github.com/SiteCraftorCPP/TGBOT-i_saidru.git .

python3 -m venv .venv
# или явно: python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"

cp .env.example .env
chmod 600 .env
nano .env   # BOT_TOKEN, DEEPSEEK_API_KEYS, DATABASE_URL, при необходимости ЮKassa
```

Отдельный системный пользователь только для этого бота (не трогает другие проекты):

```bash
sudo useradd -r -s /usr/sbin/nologin -d /srv/TGBOT-i_saidru -M tgbot-isaidru
sudo chown -R tgbot-isaidru:tgbot-isaidru /srv/TGBOT-i_saidru
```

### 2) База и миграции

PostgreSQL или SQLite — см. `.env.example`. После сохранения `.env`:

```bash
source .venv/bin/activate
cd /srv/TGBOT-i_saidru
alembic upgrade head
```

### 3) ЮKassa webhook (опционально, отдельный location в nginx)

Порт **`YOOKASSA_WEBHOOK_PORT`** из `.env` должен совпадать с `proxy_pass`. Пример для **вашего** домена (добавьте блок в уже существующий `server { }`, другие сайты не трогайте):

```nginx
location /yookassa/webhook {
    proxy_pass http://127.0.0.1:8890;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

(Если в `.env` другой порт — замените `8890`; путь должен совпасть с `YOOKASSA_WEBHOOK_PATH`.)

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 4) systemd — автозапуск

В репозитории лежит готовый unit: `deploy/systemd/tgbot-isaidru.service`.

```bash
sudo cp /srv/TGBOT-i_saidru/deploy/systemd/tgbot-isaidru.service /etc/systemd/system/
# если ставили проект не в /srv/TGBOT-i_saidru — отредактируйте пути в unit и сохраните
sudo nano /etc/systemd/system/tgbot-isaidru.service

sudo systemctl daemon-reload
sudo systemctl enable --now tgbot-isaidru.service
journalctl -u tgbot-isaidru -f
```

Статус: `systemctl status tgbot-isaidru`

После `git pull` обновления: `sudo systemctl restart tgbot-isaidru`

На сервере нужны Python 3.11+, для PDF — пакет `libreoffice` (или `soffice` в `PATH` / переменная `LIBREOFFICE_PATH` в `.env`).

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
