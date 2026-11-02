# 📘 DR Backup Verifier — Guide / Гайд / Посібник

**🌐 Language / Язык / Мова:**
[🇬🇧 English](#-english) · [🇷🇺 Русский](#-русский) · [🇺🇦 Українська](#-українська)

---

# 🇬🇧 English

## 1. What is this, in plain words?

Everyone makes database backups. Almost nobody checks that a backup **actually works** — until the day
the database dies and the backup turns out to be broken. That is the worst moment to find out.

**DR Backup Verifier** does the check for you, automatically:

1. It makes a backup (a "dump") of your database.
2. It starts a **temporary, isolated Docker container** (a throw-away mini-server with no internet access).
3. It **restores the backup** into that container, like in a real disaster.
4. It **checks the data**: same tables, same columns, same number of rows, no corruption, plus your own checks.
5. It **deletes the temporary container** and tells you: ✅ backup is good, or ❌ backup is broken.

Your real database is only *read* (to make the dump). Nothing is ever changed or deleted in it.

> **Glossary.** *Backup / dump* — a file with a copy of your database. *Restore* — rebuilding the database from that file.
> *Container* — an isolated mini-computer run by Docker. *RTO* — how long recovery takes. *RPO* — how old your newest backup is.
> *Config* — a small text settings file.

## 2. What you need (one time)

| You need | How to check | Where to get it |
|---|---|---|
| **Python 3.11 or newer** | `python3 --version` (Windows: `python --version`) | https://www.python.org/downloads/ |
| **Docker** (running!) | `docker --version` and `docker info` | https://docs.docker.com/get-docker/ |
| Your database in PostgreSQL or MySQL | — | — |

- **Windows / macOS:** install *Docker Desktop* and **start it** (whale icon in the tray must be steady).
- **Linux:** if you see `permission denied` for Docker, run `sudo usermod -aG docker $USER`, then log out and in again.
- No `pip install` is needed — the project uses only built-in Python.

## 3. Unpack and open a terminal

Unzip `dr-backup-verifier.zip`. Open a terminal **inside the unpacked folder** (the one that contains `README.md`).

- **Windows:** open the folder, click the address bar, type `cmd`, press Enter. Use `python` instead of `python3` everywhere below.
- **macOS/Linux:** right-click → *Open in Terminal*, or `cd path/to/dr-backup-verifier`.

## 4. Safe first try (no real data touched)

### 4.1 Self-test (does not need Docker)
```bash
python3 -m unittest discover -s tests -t . -v
```
Expected ending: `Ran 24 tests … OK`.

### 4.2 Full demo (needs Docker)
It starts a sample database, backs it up, restores it in a temporary container, and even breaks a backup on purpose to show it gets caught.
```bash
./scripts/demo.sh        # Linux / macOS
```
**Windows:** run it from *WSL* or *Git Bash*, or follow the manual steps:
```bash
docker compose -f demo/docker-compose.yml up -d
python -m verifier -c demo/config.toml run
docker compose -f demo/docker-compose.yml down -v
```
Wait ~10–20 seconds after `up -d` before `run` (the sample DB must start). The first run downloads the Postgres image — it can take a few minutes.

## 5. Use it with YOUR database

### Step 1 — create your config
```bash
cp config.example.toml config.toml        # Windows: copy config.example.toml config.toml
```
Open `config.toml` in any text editor (Notepad is fine). Change only what you need:

```toml
[project]
name = "myapp"              # any short name; used in backup file names
retention_keep = 7          # keep the last 7 backups, delete older ones

[source]
type = "postgres"           # postgres  or  mysql
database = "shop"           # your database name
user = "app"                # database user
container = "my_prod_db"    # if your DB runs in Docker: the container NAME (see: docker ps)
password_env = "SOURCE_DB_PASSWORD"   # NAME of an environment variable that holds the password
```

**Two ways to reach your database** (pick one):

- **A. Database runs in a Docker container** → set `container = "name"`. Nothing else to install.
- **B. Database is elsewhere (server, cloud)** → delete the `container` line and set `host = "…"` and `port = …`.
  You must have `pg_dump`/`psql` (PostgreSQL) or `mysqldump`/`mysql` (MySQL) installed on this computer.

**Important:** `[verify].image` should be the **same major version as production** (e.g. prod is PostgreSQL 15 → `image = "postgres:15-alpine"`;
MySQL 8.0 → `image = "mysql:8.0"`). Otherwise you test the wrong thing.

### Step 2 — give the password (never write it into the file)
```bash
export SOURCE_DB_PASSWORD='your-password'          # Linux / macOS
```
```powershell
$env:SOURCE_DB_PASSWORD = "your-password"          # Windows PowerShell
```
If your DB inside the container allows local connections without a password, you may skip this and delete `password_env`.

### Step 3 — run
```bash
python3 -m verifier -c config.toml run
```
That's it. It makes a backup, verifies it, and prints a report.

## 6. All commands

> ⚠️ Options `-c` and `-v` go **before** the command: `python3 -m verifier -c config.toml -v run`.

| Command | What it does | When to use |
|---|---|---|
| `run` | backup **+** verification **+** old-backup cleanup | daily, the main mode |
| `backup` | only makes a backup | when you want verification later |
| `verify` | verifies the **latest** backup | after `backup`, or to re-check |
| `verify --backup path/to/file.dump` | verifies a specific file | check an old/downloaded backup |
| `list` | shows backups and whether each was verified | "what do I have?" |
| `cleanup` | removes leftover temporary containers/networks | after a crash or power loss |

Extra options:

| Option | Meaning |
|---|---|
| `-c FILE` | which config to use (default: `config.toml` in the current folder) |
| `-v` | verbose log (for troubleshooting) |
| `--keep-on-failure` | on failure, do **not** delete the test container so you can investigate (then run `cleanup`) |
| `--skip-checksum` | skip the SHA-256 check (demo/diagnostics only) |

## 7. Reading the result

```
(on screen the program prints this in Russian — same lines, same order)
=== Backup verification: PASSED ✅ ===
[ OK ] backup_file_integrity     SHA-256 matches, 3.41 MiB  (0.0s)
[ OK ] provision_isolated_container   …
[ OK ] restore                   dump restored without errors  (4.2s)
[ OK ] row_counts_match          2 tables, 55000 rows — matches the source
[ OK ] cleanup                   test container, network and data removed
  restore_sec (measured RTO): 4.2
```

- **`PASSED ✅`** — the backup is restorable. 
- **`FAILED ❌`** — find the line that starts with `[FAIL]`; the text after it says what went wrong.

**Exit code** (useful for automation): `0` = good · `1` = backup failed verification · `2` = could not run (bad config, Docker off, …).

**Where are the files?**
- `backups/` — your backups (`.dump` for PostgreSQL, `.sql.gz` for MySQL) + a `.manifest.json` next to each. **Never delete a manifest by hand** — verification needs it.
- `reports/` — JSON reports; `reports/latest.json` is always the newest.

## 8. What exactly is checked

1. File is intact (size + SHA-256 vs. the manifest).
2. The temp container is isolated (internal network, no ports published, memory/CPU limits, random one-time password).
3. The archive is readable.
4. The restore finishes with no errors; the time is measured (**RTO**).
5. Schema (tables, columns, types) equals the source.
6. Row count in **every** table equals the source.
7. PostgreSQL: no invalid indexes, and all data can be read end-to-end. MySQL: `mysqlcheck`.
8. **Your own SQL checks** (see below).
9. The temp container, network and data are deleted — and it verifies they are really gone.

### Your own checks (recommended!)
Add business rules to `config.toml`. The SQL must return **one value**:
```toml
[[verify.custom_checks]]
name = "users table is not empty"
sql = "SELECT count(*) FROM users"
expect_min = 1

[[verify.custom_checks]]
name = "no orphan orders"
sql = "SELECT count(*) FROM orders o LEFT JOIN users u ON u.id = o.user_id WHERE u.id IS NULL"
expect_equals = 0
```
Use `expect_equals`, `expect_min`, `expect_max`.

### Useful optional settings in `[verify]`
```toml
rto_target_sec = 900        # fail if recovery takes longer than 15 min
rpo_target_hours = 24       # fail if the backup is older than 24 h
row_count_tolerance_percent = 0.5   # allow small differences if the DB is written to during the dump
memory = "2g"               # give more for big databases
```

## 9. Run automatically every night

**Linux/macOS (cron)** — `crontab -e`, add:
```cron
0 3 * * *  cd /opt/dr-backup-verifier && SOURCE_DB_PASSWORD='…' python3 -m verifier -c config.toml run >> /var/log/dr-verify.log 2>&1
```
**Windows:** *Task Scheduler* → Create Task → Action: `python`, arguments: `-m verifier -c config.toml run`, *Start in*: the project folder.

**Alerts:** put a webhook (Slack/Mattermost/Discord-style) or Telegram bot in `[notify]` and set the env variables it names. You get a message when verification fails.

## 10. Troubleshooting

> Note: the program's own messages are written in Russian, so the left column shows them as they appear on screen (with the English meaning next to it).

| Message | Reason → fix |
|---|---|
| `Docker недоступен` / Docker unavailable | Docker is not running → start Docker Desktop / `sudo systemctl start docker`. On Linux add yourself to the `docker` group. |
| `Конфиг не найден` / config not found | Wrong folder or name → `cd` into the project folder or pass `-c path/to/config.toml`. |
| `в … нет бэкапов` / no backups | You ran `verify` before any backup → run `run` or `backup` first. |
| `дамп завершился с кодом …` / dump failed | Wrong user/password/container name → check `docker ps`, the variable named in `password_env`. |
| `command not found: pg_dump` | You use host mode (B) without client tools → install them, or use mode A (`container`). |
| `не удалось загрузить образ` / image pull fails | No internet for the first download, or wrong `image` name. |
| `СУБД не стала доступна` / DB did not start | Big DB or slow PC → increase `startup_timeout_sec`; check `memory`. |
| `восстановление упало` / restore failed | A real problem with the backup **or** a missing extension in the test image. Read the error text; run with `--keep-on-failure -v`. |
| `row_counts_match` FAIL by a few rows | DB was being written during the dump → set `row_count_tolerance_*`, or back up at a quiet time. |
| Leftover containers named `drv-…` | Crash during a run → `python3 -m verifier cleanup`. |

Still stuck? Run with `-v` and read the log from the top — the first `ERROR` line is usually the cause.

## 11. Safety notes

- Backups contain **real data**. Restrict the `backups/` folder permissions (`chmod 700 backups`) and encrypt anything you upload elsewhere.
- Never put passwords into `config.toml` — use environment variables.
- The tool only **reads** from your production DB.
- The test container has no internet access and no published ports.

---

# 🇷🇺 Русский

## 1. Что это, простыми словами?

Бэкапы делают все. А вот проверяют, что бэкап **реально восстанавливается**, — почти никто. И узнают об этом
в день аварии, когда уже поздно.

**DR Backup Verifier** делает эту проверку за вас, автоматически:

1. Делает бэкап (дамп) вашей базы данных.
2. Поднимает **временный изолированный Docker-контейнер** (одноразовый мини-сервер без доступа в интернет).
3. **Восстанавливает бэкап** в этот контейнер — как при настоящей аварии.
4. **Проверяет данные**: те же таблицы, колонки, то же число строк, нет повреждений, плюс ваши собственные проверки.
5. **Удаляет временный контейнер** и сообщает: ✅ бэкап рабочий или ❌ бэкап сломан.

Ваша боевая база только **читается** (для снятия дампа). В ней ничего не меняется и не удаляется.

> **Словарик.** *Бэкап / дамп* — файл с копией базы. *Восстановление (restore)* — сборка базы обратно из файла.
> *Контейнер* — изолированный «мини-компьютер» под управлением Docker. *RTO* — за сколько времени восстанавливаемся.
> *RPO* — насколько «старый» самый свежий бэкап. *Конфиг* — небольшой текстовый файл с настройками.

## 2. Что нужно (один раз)

| Нужно | Как проверить | Где взять |
|---|---|---|
| **Python 3.11 или новее** | `python3 --version` (Windows: `python --version`) | https://www.python.org/downloads/ |
| **Docker** (запущенный!) | `docker --version` и `docker info` | https://docs.docker.com/get-docker/ |
| База PostgreSQL или MySQL | — | — |

- **Windows / macOS:** установите *Docker Desktop* и **запустите его** (значок кита в трее должен перестать «мигать»).
- **Linux:** если Docker пишет `permission denied`, выполните `sudo usermod -aG docker $USER` и перезайдите в систему.
- `pip install` не нужен — используется только встроенный Python.

## 3. Распакуйте и откройте терминал

Распакуйте `dr-backup-verifier.zip`. Откройте терминал **внутри распакованной папки** (где лежит `README.md`).

- **Windows:** откройте папку, кликните в адресную строку, наберите `cmd`, Enter. Ниже везде пишите `python` вместо `python3`.
- **macOS/Linux:** правый клик → *Открыть в терминале* или `cd путь/к/dr-backup-verifier`.

## 4. Безопасная первая попытка (боевые данные не затрагиваются)

### 4.1 Самотест (Docker не нужен)
```bash
python3 -m unittest discover -s tests -t . -v
```
В конце должно быть: `Ran 24 tests … OK`.

### 4.2 Полное демо (нужен Docker)
Поднимает тестовую базу, делает бэкап, восстанавливает его во временном контейнере и даже специально портит бэкап, чтобы показать, что порча ловится.
```bash
./scripts/demo.sh        # Linux / macOS
```
**Windows:** запускайте через *WSL* или *Git Bash*, либо вручную:
```bash
docker compose -f demo/docker-compose.yml up -d
python -m verifier -c demo/config.toml run
docker compose -f demo/docker-compose.yml down -v
```
После `up -d` подождите 10–20 секунд, пока демо-база стартует. При первом запуске скачивается образ Postgres — это может занять несколько минут.

## 5. Использование с ВАШЕЙ базой

### Шаг 1 — создайте свой конфиг
```bash
cp config.example.toml config.toml        # Windows: copy config.example.toml config.toml
```
Откройте `config.toml` любым текстовым редактором (подойдёт Блокнот). Меняйте только нужное:

```toml
[project]
name = "myapp"              # любое короткое имя; попадает в имена файлов бэкапов
retention_keep = 7          # хранить 7 последних бэкапов, старые удалять

[source]
type = "postgres"           # postgres  или  mysql
database = "shop"           # имя вашей базы
user = "app"                # пользователь БД
container = "my_prod_db"    # если БД в Docker: ИМЯ контейнера (смотрите: docker ps)
password_env = "SOURCE_DB_PASSWORD"   # ИМЯ переменной окружения, где лежит пароль
```

**Два способа добраться до базы** (выберите один):

- **A. База работает в Docker-контейнере** → укажите `container = "имя"`. Больше ничего ставить не нужно.
- **Б. База где-то ещё (сервер, облако)** → удалите строку `container`, укажите `host = "…"` и `port = …`.
  На этом компьютере должны быть установлены `pg_dump`/`psql` (PostgreSQL) или `mysqldump`/`mysql` (MySQL).

**Важно:** `[verify].image` должен быть **той же мажорной версии, что и на проде** (прод — PostgreSQL 15 → `image = "postgres:15-alpine"`;
MySQL 8.0 → `image = "mysql:8.0"`). Иначе вы проверяете не то.

### Шаг 2 — передайте пароль (никогда не пишите его в файл)
```bash
export SOURCE_DB_PASSWORD='ваш-пароль'          # Linux / macOS
```
```powershell
$env:SOURCE_DB_PASSWORD = "ваш-пароль"          # Windows PowerShell
```
Если БД в контейнере пускает локальные подключения без пароля, этот шаг можно пропустить и удалить `password_env`.

### Шаг 3 — запуск
```bash
python3 -m verifier -c config.toml run
```
Готово: программа сделает бэкап, проверит его и выведет отчёт.

## 6. Все команды

> ⚠️ Опции `-c` и `-v` пишутся **перед** командой: `python3 -m verifier -c config.toml -v run`.

| Команда | Что делает | Когда использовать |
|---|---|---|
| `run` | бэкап **+** проверка **+** удаление старых бэкапов | каждый день, основной режим |
| `backup` | только снять бэкап | если проверять будете позже |
| `verify` | проверить **последний** бэкап | после `backup` или для повторной проверки |
| `verify --backup путь/к/файлу.dump` | проверить конкретный файл | старый или скачанный бэкап |
| `list` | список бэкапов и статус проверки | «что у меня есть?» |
| `cleanup` | удалить «осиротевшие» временные контейнеры и сети | после сбоя или отключения питания |

Дополнительные опции:

| Опция | Значение |
|---|---|
| `-c ФАЙЛ` | какой конфиг использовать (по умолчанию `config.toml` в текущей папке) |
| `-v` | подробный лог (для поиска проблем) |
| `--keep-on-failure` | при провале **не** удалять тестовый контейнер, чтобы разобраться (потом выполните `cleanup`) |
| `--skip-checksum` | пропустить проверку SHA-256 (только демо/диагностика) |

## 7. Как читать результат

```
=== Проверка бэкапа: ПРОЙДЕНА ✅ ===
[ OK ] backup_file_integrity      SHA-256 совпадает, 3.41 MiB  (0.0s)
[ OK ] provision_isolated_container   …
[ OK ] restore                    дамп восстановлен без ошибок  (4.2s)
[ OK ] row_counts_match           2 таблиц, 55000 строк — совпадает с источником
[ OK ] cleanup                    тестовые контейнер, сеть и данные удалены
  restore_sec (измеренный RTO): 4.2
```

- **`ПРОЙДЕНА ✅`** — бэкап восстанавливается.
- **`ПРОВАЛЕНА ❌`** — найдите строку, начинающуюся с `[FAIL]`: текст после неё объясняет, что не так.

**Код выхода** (полезен для автоматизации): `0` = всё хорошо · `1` = бэкап не прошёл проверку · `2` = запуск не удался (плохой конфиг, Docker выключен и т.п.).

**Где лежат файлы?**
- `backups/` — ваши бэкапы (`.dump` для PostgreSQL, `.sql.gz` для MySQL) и рядом файл `.manifest.json`. **Не удаляйте манифесты вручную** — без них проверка невозможна.
- `reports/` — JSON-отчёты; `reports/latest.json` — всегда самый свежий.

## 8. Что именно проверяется

1. Файл цел (размер и SHA-256 против манифеста).
2. Тестовый контейнер изолирован (внутренняя сеть, порты не публикуются, лимиты памяти/CPU, случайный одноразовый пароль).
3. Архив читается.
4. Восстановление завершается без ошибок; время замеряется (**RTO**).
5. Схема (таблицы, колонки, типы) совпадает с источником.
6. Число строк в **каждой** таблице совпадает с источником.
7. PostgreSQL: нет невалидных индексов, все данные читаются от начала до конца. MySQL: `mysqlcheck`.
8. **Ваши собственные SQL-проверки** (см. ниже).
9. Контейнер, сеть и данные удалены — и программа проверяет, что они действительно исчезли.

### Ваши собственные проверки (рекомендуется!)
Добавьте бизнес-правила в `config.toml`. SQL должен вернуть **одно значение**:
```toml
[[verify.custom_checks]]
name = "таблица users не пустая"
sql = "SELECT count(*) FROM users"
expect_min = 1

[[verify.custom_checks]]
name = "нет заказов-сирот"
sql = "SELECT count(*) FROM orders o LEFT JOIN users u ON u.id = o.user_id WHERE u.id IS NULL"
expect_equals = 0
```
Доступны `expect_equals`, `expect_min`, `expect_max`.

### Полезные необязательные настройки в `[verify]`
```toml
rto_target_sec = 900        # провал, если восстановление дольше 15 минут
rpo_target_hours = 24       # провал, если бэкап старше 24 часов
row_count_tolerance_percent = 0.5   # допуск, если в БД пишут во время дампа
memory = "2g"               # для больших баз выделите больше
```

## 9. Запуск каждую ночь автоматически

**Linux/macOS (cron)** — `crontab -e`, добавьте:
```cron
0 3 * * *  cd /opt/dr-backup-verifier && SOURCE_DB_PASSWORD='…' python3 -m verifier -c config.toml run >> /var/log/dr-verify.log 2>&1
```
**Windows:** *Планировщик заданий* → Создать задачу → Действие: `python`, аргументы: `-m verifier -c config.toml run`, *Рабочая папка*: папка проекта.

**Оповещения:** укажите webhook (в стиле Slack/Mattermost/Discord) или Telegram-бота в `[notify]` и задайте переменные окружения, названия которых там указаны. При провале проверки придёт сообщение.

## 10. Если что-то пошло не так

| Сообщение | Причина → решение |
|---|---|
| `Docker недоступен` | Docker не запущен → запустите Docker Desktop / `sudo systemctl start docker`. На Linux добавьте себя в группу `docker`. |
| `Конфиг не найден` | Не та папка или имя → перейдите в папку проекта или укажите `-c путь/к/config.toml`. |
| `в … нет бэкапов` | Вызвали `verify` до первого бэкапа → сначала `run` или `backup`. |
| `дамп завершился с кодом …` | Неверный пользователь/пароль/имя контейнера → проверьте `docker ps` и переменную из `password_env`. |
| `команда не найдена: pg_dump` | Режим Б без клиентских утилит → установите их или используйте режим A (`container`). |
| `не удалось загрузить образ` | Нет интернета при первой загрузке или неверное имя `image`. |
| `СУБД не стала доступна` | Большая БД или медленный ПК → увеличьте `startup_timeout_sec`, проверьте `memory`. |
| `восстановление упало` | Реальная проблема бэкапа **или** в тестовом образе нет нужного расширения. Прочтите текст ошибки; запустите с `--keep-on-failure -v`. |
| `row_counts_match` FAIL на несколько строк | В БД писали во время дампа → задайте `row_count_tolerance_*` или делайте бэкап в тихое время. |
| Остались контейнеры `drv-…` | Сбой во время запуска → `python3 -m verifier cleanup`. |

Не получилось? Запустите с `-v` и читайте лог сверху: первая строка `ERROR` обычно и есть причина.

## 11. Безопасность

- Бэкапы содержат **реальные данные**. Ограничьте права на папку `backups/` (`chmod 700 backups`) и шифруйте всё, что выгружаете наружу.
- Никогда не пишите пароли в `config.toml` — используйте переменные окружения.
- Программа только **читает** из боевой БД.
- У тестового контейнера нет доступа в интернет и нет открытых портов.

---

# 🇺🇦 Українська

## 1. Що це, простими словами?

Бекапи роблять усі. А от перевіряють, що бекап **справді відновлюється**, — майже ніхто. І дізнаються про це
в день аварії, коли вже пізно.

**DR Backup Verifier** робить цю перевірку за вас, автоматично:

1. Робить бекап (дамп) вашої бази даних.
2. Піднімає **тимчасовий ізольований Docker-контейнер** (одноразовий міні-сервер без доступу до інтернету).
3. **Відновлює бекап** у цей контейнер — як під час справжньої аварії.
4. **Перевіряє дані**: ті самі таблиці, колонки, та сама кількість рядків, немає пошкоджень, а також ваші власні перевірки.
5. **Видаляє тимчасовий контейнер** і повідомляє: ✅ бекап робочий або ❌ бекап зламаний.

Ваша бойова база лише **читається** (для зняття дампа). У ній нічого не змінюється і не видаляється.

> **Словничок.** *Бекап / дамп* — файл із копією бази. *Відновлення (restore)* — збирання бази назад із файлу.
> *Контейнер* — ізольований «міні-комп'ютер» під керуванням Docker. *RTO* — за який час відновлюємось.
> *RPO* — наскільки «старий» найсвіжіший бекап. *Конфіг* — невеликий текстовий файл із налаштуваннями.

## 2. Що потрібно (один раз)

| Потрібно | Як перевірити | Де взяти |
|---|---|---|
| **Python 3.11 або новіший** | `python3 --version` (Windows: `python --version`) | https://www.python.org/downloads/ |
| **Docker** (запущений!) | `docker --version` та `docker info` | https://docs.docker.com/get-docker/ |
| База PostgreSQL або MySQL | — | — |

- **Windows / macOS:** встановіть *Docker Desktop* і **запустіть його** (значок кита в треї має перестати «блимати»).
- **Linux:** якщо Docker пише `permission denied`, виконайте `sudo usermod -aG docker $USER` і перезайдіть у систему.
- `pip install` не потрібен — використовується лише вбудований Python.

## 3. Розпакуйте й відкрийте термінал

Розпакуйте `dr-backup-verifier.zip`. Відкрийте термінал **всередині розпакованої теки** (де лежить `README.md`).

- **Windows:** відкрийте теку, клацніть в адресний рядок, наберіть `cmd`, Enter. Нижче скрізь пишіть `python` замість `python3`.
- **macOS/Linux:** права кнопка → *Відкрити в терміналі* або `cd шлях/до/dr-backup-verifier`.

## 4. Безпечна перша спроба (бойові дані не зачіпаються)

### 4.1 Самотест (Docker не потрібен)
```bash
python3 -m unittest discover -s tests -t . -v
```
Наприкінці має бути: `Ran 24 tests … OK`.

### 4.2 Повне демо (потрібен Docker)
Піднімає тестову базу, робить бекап, відновлює його в тимчасовому контейнері і навіть спеціально псує бекап, щоб показати, що пошкодження ловиться.
```bash
./scripts/demo.sh        # Linux / macOS
```
**Windows:** запускайте через *WSL* або *Git Bash*, або вручну:
```bash
docker compose -f demo/docker-compose.yml up -d
python -m verifier -c demo/config.toml run
docker compose -f demo/docker-compose.yml down -v
```
Після `up -d` зачекайте 10–20 секунд, доки демо-база стартує. Під час першого запуску завантажується образ Postgres — це може тривати кілька хвилин.

## 5. Використання з ВАШОЮ базою

### Крок 1 — створіть свій конфіг
```bash
cp config.example.toml config.toml        # Windows: copy config.example.toml config.toml
```
Відкрийте `config.toml` будь-яким текстовим редактором (підійде Блокнот). Змінюйте лише потрібне:

```toml
[project]
name = "myapp"              # будь-яка коротка назва; потрапляє в назви файлів бекапів
retention_keep = 7          # зберігати 7 останніх бекапів, старі видаляти

[source]
type = "postgres"           # postgres  або  mysql
database = "shop"           # назва вашої бази
user = "app"                # користувач БД
container = "my_prod_db"    # якщо БД у Docker: НАЗВА контейнера (дивіться: docker ps)
password_env = "SOURCE_DB_PASSWORD"   # НАЗВА змінної середовища, де лежить пароль
```

**Два способи дістатися до бази** (оберіть один):

- **A. База працює в Docker-контейнері** → вкажіть `container = "назва"`. Більше нічого ставити не треба.
- **Б. База деінде (сервер, хмара)** → видаліть рядок `container`, вкажіть `host = "…"` та `port = …`.
  На цьому комп'ютері мають бути встановлені `pg_dump`/`psql` (PostgreSQL) або `mysqldump`/`mysql` (MySQL).

**Важливо:** `[verify].image` має бути **тієї ж мажорної версії, що й на проді** (прод — PostgreSQL 15 → `image = "postgres:15-alpine"`;
MySQL 8.0 → `image = "mysql:8.0"`). Інакше ви перевіряєте не те.

### Крок 2 — передайте пароль (ніколи не пишіть його у файл)
```bash
export SOURCE_DB_PASSWORD='ваш-пароль'          # Linux / macOS
```
```powershell
$env:SOURCE_DB_PASSWORD = "ваш-пароль"          # Windows PowerShell
```
Якщо БД у контейнері дозволяє локальні підключення без пароля, цей крок можна пропустити й видалити `password_env`.

### Крок 3 — запуск
```bash
python3 -m verifier -c config.toml run
```
Готово: програма зробить бекап, перевірить його й виведе звіт.

## 6. Усі команди

> ⚠️ Опції `-c` та `-v` пишуться **перед** командою: `python3 -m verifier -c config.toml -v run`.

| Команда | Що робить | Коли використовувати |
|---|---|---|
| `run` | бекап **+** перевірка **+** видалення старих бекапів | щодня, основний режим |
| `backup` | лише зняти бекап | якщо перевірятимете пізніше |
| `verify` | перевірити **останній** бекап | після `backup` або для повторної перевірки |
| `verify --backup шлях/до/файлу.dump` | перевірити конкретний файл | старий або завантажений бекап |
| `list` | список бекапів і статус перевірки | «що в мене є?» |
| `cleanup` | видалити «осиротілі» тимчасові контейнери й мережі | після збою або відключення живлення |

Додаткові опції:

| Опція | Значення |
|---|---|
| `-c ФАЙЛ` | який конфіг використовувати (типово `config.toml` у поточній теці) |
| `-v` | докладний лог (для пошуку проблем) |
| `--keep-on-failure` | у разі провалу **не** видаляти тестовий контейнер, щоб розібратися (потім виконайте `cleanup`) |
| `--skip-checksum` | пропустити перевірку SHA-256 (лише демо/діагностика) |

## 7. Як читати результат

```
=== Перевірка бекапа: ПРОЙДЕНО ✅ ===
[ OK ] backup_file_integrity      SHA-256 збігається, 3.41 MiB  (0.0s)
[ OK ] provision_isolated_container   …
[ OK ] restore                    дамп відновлено без помилок  (4.2s)
[ OK ] row_counts_match           2 таблиць, 55000 рядків — збігається з джерелом
[ OK ] cleanup                    тестові контейнер, мережу й дані видалено
  restore_sec (виміряний RTO): 4.2
```

> Примітка: сама програма виводить звіт російською мовою; зміст рядків такий самий, як у наведеному прикладі. Англійський вигляд цього ж звіту — у розділі English.

- **`ПРОЙДЕНО ✅`** — бекап відновлюється.
- **`ПРОВАЛЕНО ❌`** — знайдіть рядок, що починається з `[FAIL]`: текст після нього пояснює, що не так.

**Код виходу** (корисний для автоматизації): `0` = усе добре · `1` = бекап не пройшов перевірку · `2` = запуск не вдався (поганий конфіг, Docker вимкнений тощо).

**Де лежать файли?**
- `backups/` — ваші бекапи (`.dump` для PostgreSQL, `.sql.gz` для MySQL) і поруч файл `.manifest.json`. **Не видаляйте маніфести вручну** — без них перевірка неможлива.
- `reports/` — JSON-звіти; `reports/latest.json` — завжди найсвіжіший.

## 8. Що саме перевіряється

1. Файл цілий (розмір і SHA-256 проти маніфесту).
2. Тестовий контейнер ізольований (внутрішня мережа, порти не публікуються, ліміти пам'яті/CPU, випадковий одноразовий пароль).
3. Архів читається.
4. Відновлення завершується без помилок; час вимірюється (**RTO**).
5. Схема (таблиці, колонки, типи) збігається з джерелом.
6. Кількість рядків у **кожній** таблиці збігається з джерелом.
7. PostgreSQL: немає невалідних індексів, усі дані читаються від початку до кінця. MySQL: `mysqlcheck`.
8. **Ваші власні SQL-перевірки** (див. нижче).
9. Контейнер, мережу й дані видалено — і програма перевіряє, що вони справді зникли.

### Ваші власні перевірки (рекомендовано!)
Додайте бізнес-правила в `config.toml`. SQL має повернути **одне значення**:
```toml
[[verify.custom_checks]]
name = "таблиця users не порожня"
sql = "SELECT count(*) FROM users"
expect_min = 1

[[verify.custom_checks]]
name = "немає замовлень-сиріт"
sql = "SELECT count(*) FROM orders o LEFT JOIN users u ON u.id = o.user_id WHERE u.id IS NULL"
expect_equals = 0
```
Доступні `expect_equals`, `expect_min`, `expect_max`.

### Корисні необов'язкові налаштування в `[verify]`
```toml
rto_target_sec = 900        # провал, якщо відновлення триває довше 15 хвилин
rpo_target_hours = 24       # провал, якщо бекап старший за 24 години
row_count_tolerance_percent = 0.5   # допуск, якщо в БД пишуть під час дампа
memory = "2g"               # для великих баз виділіть більше
```

## 9. Запуск щоночі автоматично

**Linux/macOS (cron)** — `crontab -e`, додайте:
```cron
0 3 * * *  cd /opt/dr-backup-verifier && SOURCE_DB_PASSWORD='…' python3 -m verifier -c config.toml run >> /var/log/dr-verify.log 2>&1
```
**Windows:** *Планувальник завдань* → Створити завдання → Дія: `python`, аргументи: `-m verifier -c config.toml run`, *Робоча тека*: тека проєкту.

**Сповіщення:** вкажіть webhook (у стилі Slack/Mattermost/Discord) або Telegram-бота в `[notify]` і задайте змінні середовища, назви яких там зазначені. У разі провалу перевірки прийде повідомлення.

## 10. Якщо щось пішло не так

| Повідомлення | Причина → рішення |
|---|---|
| `Docker недоступен` | Docker не запущений → запустіть Docker Desktop / `sudo systemctl start docker`. На Linux додайте себе до групи `docker`. |
| `Конфиг не найден` | Не та тека чи назва → перейдіть у теку проєкту або вкажіть `-c шлях/до/config.toml`. |
| `в … нет бэкапов` | Викликали `verify` до першого бекапа → спочатку `run` або `backup`. |
| `дамп завершился с кодом …` | Невірний користувач/пароль/назва контейнера → перевірте `docker ps` і змінну з `password_env`. |
| `команда не найдена: pg_dump` | Режим Б без клієнтських утиліт → встановіть їх або використовуйте режим A (`container`). |
| `не удалось загрузить образ` | Немає інтернету під час першого завантаження або невірна назва `image`. |
| `СУБД не стала доступна` | Велика БД або повільний ПК → збільште `startup_timeout_sec`, перевірте `memory`. |
| `восстановление упало` | Реальна проблема бекапа **або** в тестовому образі немає потрібного розширення. Прочитайте текст помилки; запустіть із `--keep-on-failure -v`. |
| `row_counts_match` FAIL на кілька рядків | У БД писали під час дампа → задайте `row_count_tolerance_*` або робіть бекап у тихий час. |
| Лишилися контейнери `drv-…` | Збій під час запуску → `python3 -m verifier cleanup`. |

Не вийшло? Запустіть із `-v` і читайте лог зверху: перший рядок `ERROR` зазвичай і є причиною.

## 11. Безпека

- Бекапи містять **реальні дані**. Обмежте права на теку `backups/` (`chmod 700 backups`) і шифруйте все, що вивантажуєте назовні.
- Ніколи не пишіть паролі в `config.toml` — використовуйте змінні середовища.
- Програма лише **читає** з бойової БД.
- Тестовий контейнер не має доступу до інтернету й відкритих портів.

---

<sub>Technical overview and architecture: see [README.md](README.md). · Технічний огляд: [README.md](README.md).</sub>
