# Qalqan Platform — MCP-сервер для Оксимы

Коннектор для Claude, через который сотрудники Qalqan работают с правовой базой
[Оксимы](https://app.oxima.kz): НПА Казахстана и МФЦА, судебные дела с ИИ-аннотациями
и ИИ-ассистент Оксимы.

Сервер работает круглосуточно на облачной машине Google Cloud (e2-micro) и доступен
по адресу `https://mcp.qalqan.org/mcp` через Cloudflare Tunnel. Войти могут только
Google-аккаунты `@qalqan.org`.

## Облачный сервер

Ubuntu 24.04, две службы systemd: `qalqan-mcp` (сервер) и `qalqan-tunnel` (cloudflared).
Обе стартуют сами при загрузке и перезапускаются при падении.

| Что | Где |
|---|---|
| Код | `/opt/qalqan-mcp` (клон этого репозитория) |
| Настройки | `/opt/qalqan-mcp/.env`, токен туннеля — `/etc/qalqan-mcp/tunnel.env` |
| OAuth-токены | `/var/lib/qalqan-mcp/oauth-proxy` |
| Журналы | `journalctl -u qalqan-mcp`, `journalctl -u qalqan-tunnel` |

Вход по SSH-ключу администратора (адрес — в консоли Google Cloud → VM instances).

Обновить код после изменений в репозитории:

```bash
sudo -u qalqan git -C /opt/qalqan-mcp pull && sudo systemctl restart qalqan-mcp
```

Новый сервер с нуля: скопировать `.env` (и при переносе — папку хранилища
`oauth-proxy/<id>`), затем `sudo bash deploy/setup.sh /tmp/.env [/tmp/<id>]`.
Пока работает старая копия, ставьте с `NO_START=1`: две копии на одном туннеле
делят запросы, а токены у каждой свои.

Расходы: машина и диск входят в Free Tier, внешний IPv4 ≈ 3,65 $/мес. Бюджет
«qalqan-mcp 5 USD» шлёт письмо администратору на 50/90/100 %.

## Запуск на Windows (запасной вариант)

Ниже — запуск на обычном компьютере. Задача автозапуска `Qalqan MCP` на ноутбуке
отключена после переезда в облако; не включайте её, пока работает облачный сервер.

## Инструменты

| Инструмент | Что делает |
|---|---|
| `search_documents` | Поиск НПА РК (`adilet`) или актов МФЦА (`aifc`) по названию, номеру, органу |
| `get_document` | Текст документа по частям; `find="Статья 178."` — сразу к нужной статье |
| `get_document_info` | Реквизиты документа и история редакций |
| `search_cases` | Поиск судебных дел: смысловой ИИ-поиск по фабуле/позициям и фильтры |
| `get_case` | Карточка дела с ходом дела и ИИ-аннотацией |
| `find_case_party`, `find_judge` | id сторон и судей для фильтров |
| `list_handbook` | Справочники: категории дел, суды, результаты, типы документов, акты |
| `ask_oxima_ai` | Вопрос ИИ-ассистенту Оксимы (расходует лимит аккаунта) |

Все инструменты, кроме `ask_oxima_ai`, только читают данные. Ничего не удаляют и не меняют.

Публичной документации API у Оксимы нет: эндпоинты восстановлены по веб-приложению
`app.oxima.kz`, поэтому после обновлений Оксимы что-то может перестать работать.

## Установка

Нужны Python 3.12+ и `cloudflared`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env   # и заполнить
```

### 1. OAuth-клиент Google

1. <https://console.cloud.google.com> → создать проект (например, `qalqan-mcp`).
2. **APIs & Services → OAuth consent screen**: тип **Internal**. Тогда войти смогут
   только аккаунты Google Workspace `qalqan.org`, а сервер дополнительно проверяет домен сам.
   Scopes: `openid`, `.../auth/userinfo.email`.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**
   - Authorized redirect URIs: `https://mcp.qalqan.org/auth/callback`
4. Client ID и Client secret вписать в `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

### 2. Запуск сервера

```powershell
.\start.ps1
```

`start.ps1` поднимает сервер на `http://127.0.0.1:3847` и туннель Cloudflare (если в `.env`
задан `TUNNEL_TOKEN`). Каждый из них перезапускается, если упадёт. Логи лежат в `logs\server.log`
и `logs\tunnel.log`. Проверка: <http://127.0.0.1:3847/health>.

Чтобы всё стартовало само при входе в Windows, запустите `install-autostart.ps1`.
Он создаёт задачу планировщика `Qalqan MCP`.

### 3. Cloudflare Tunnel

Туннель `qalqan-hub` управляется локально: маршрут `mcp.qalqan.org → http://127.0.0.1:3847`
задан в [cloudflared.yml](cloudflared.yml), а не в панели Cloudflare.

1. Cloudflare → **Networking → Tunnels → qalqan-hub** → блок «Install cloudflared connector»
   → скопировать токен из команды `cloudflared.exe service install <токен>`.
2. Вписать его в `.env`: `TUNNEL_TOKEN=<токен>`.
3. Перезапустить `start.ps1`. Через полминуты статус туннеля должен стать **Healthy**.
4. Проверка: `https://mcp.qalqan.org/health` должен вернуть `{"status":"ok"}`.

Права администратора и служба Windows не нужны: cloudflared запускается из `start.ps1`.

### 4. Подключение в Claude

Claude → **Settings → Connectors → Add custom connector** → URL `https://mcp.qalqan.org/mcp`.
Поля OAuth Client ID/Secret оставьте пустыми: Claude зарегистрируется на сервере сам.
Коннектор должен быть настроен **с входом**. Если он создан как «без входа», Claude
выдаст ошибку «set up as not requiring sign-in» — удалите его и добавьте заново.
Откроется вход Google: выберите рабочий аккаунт `@qalqan.org`.

## Локальная проверка без авторизации

В `.env` поставить `AUTH_DISABLED=1` (работает только при `HOST=127.0.0.1`), запустить
`server.py`, подключиться MCP-клиентом к `http://127.0.0.1:3847/mcp`. Не забудьте вернуть `0`.

## Секреты

`.env` (токены Оксимы и туннеля, секрет Google, ключ подписи) в git не попадает. Если токен Оксимы
утёк, выпустите новый в Оксиме и замените его в `.env`. Если сменить `JWT_SIGNING_KEY`,
всем сотрудникам придётся войти заново.
