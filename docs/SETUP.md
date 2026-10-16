# CloudSched – Setup from zero (Windows; Linux notes at the end)

## 1. Install (in this order)
| Tool | Link | Notes |
|---|---|---|
| Python 3.12 | https://www.python.org/downloads/ | Tick **Add python.exe to PATH** |
| Git | https://git-scm.com/downloads | Then create an empty repo on https://github.com/new |
| MySQL 8 (Installer: Server + Workbench) | https://dev.mysql.com/downloads/installer/ | Choose "Server only" + Workbench; remember the root password; keep port 3306 |
| VS Code | https://code.visualstudio.com/ | Extensions: Python, MySQL (optional) |
| Docker Desktop (needed from Phase E) | https://www.docker.com/products/docker-desktop/ | Enable WSL2; check with `docker run hello-world` |

Check: `python --version`, `git --version`, `mysql --version`, `docker --version`.

## 2. How the pieces are bound together
```
.env (DATABASE_URL)  ──►  SQLAlchemy engine (app/database/session.py)  ──►  MySQL "cloudsched"
                                                                              ▲ user: cloudsched_user
uvicorn app.main:app  ──►  FastAPI routes ──► services ──► repositories ──────┘
Browser / Swagger (/docs) ──► http://127.0.0.1:8000/api/...
Docker Desktop (Phase E)  ──► docker Python SDK talks to the local Docker daemon automatically
```
Only `.env` holds secrets. It is git-ignored; `.env.example` is the template.

## 3. Create the database and a limited user (MySQL Workbench or `mysql -u root -p`)
```sql
CREATE DATABASE IF NOT EXISTS cloudsched CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'cloudsched_user'@'localhost' IDENTIFIED BY 'ChooseAStrongPassword1';
GRANT ALL PRIVILEGES ON cloudsched.* TO 'cloudsched_user'@'localhost';
FLUSH PRIVILEGES;
```
Then load the schema: `mysql -u cloudsched_user -p cloudsched < database\schema.sql`
(if you use `schema.sql` as is, it also runs `CREATE DATABASE`/`USE`, which is harmless).

## 4. Python environment and config
```
cd cloudsched\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```
Edit `.env`:
`DATABASE_URL=mysql+pymysql://cloudsched_user:ChooseAStrongPassword1@127.0.0.1:3306/cloudsched`
If the password has `@ : / #`, URL-encode it (`@` = `%40`).

## 5. Run and verify
```
pytest                                   # all tests
uvicorn app.main:app --reload            # http://127.0.0.1:8000/docs
```
Verify binding: POST a job in /docs, then in Workbench run `SELECT * FROM cloudsched.jobs;`.

## 6. Common errors
* `Access denied for user` -> wrong password/user in `.env`, or GRANT not run.
* `Can't connect to MySQL server` -> MySQL service not running (Services -> MySQL80 -> Start).
* `cryptography package is required` -> `pip install cryptography` (already in requirements).
* `ModuleNotFoundError: app` -> run pytest/uvicorn from the `backend` folder.

## 7. Git
```
git init && git add . && git commit -m "Phase A + B core"
git branch -M main && git remote add origin <your repo URL> && git push -u origin main
```

## Linux differences
`source .venv/bin/activate`, `cp .env.example .env`, `sudo apt install mysql-server`,
and for Docker add yourself to the group: `sudo usermod -aG docker $USER` (then re-login).
