.PHONY: up up-docker down reset test
up:          ## MySQL + API + UI, simulated jobs
	docker compose up --build
up-docker:   ## same, but jobs run as real containers
	docker compose -f docker-compose.yml -f docker-compose.docker.yml up --build
down:
	docker compose down
reset:       ## wipe the database volume (re-runs database/01..05 on next `up`)
	docker compose down -v
test:
	cd backend && python -m pytest -q
