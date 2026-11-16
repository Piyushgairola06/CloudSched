# Setup guide

The authoritative instructions are in the project-root `README.md` (Docker: `docker compose up --build`).

Without Docker: install MySQL with `database/install.sql` (it runs `01_schema.sql` … `05_seed_data.sql`; never install
`schema.sql` alone), copy `backend/.env.example` to `backend/.env`, then run `uvicorn app.main:app --reload` from `backend/`.
UI: `http://127.0.0.1:8000/`, API docs: `/docs`.
