.PHONY: up down sync-local sync-drive test typecheck

up:          ## Bring up the Postgres database (docker compose)
	docker compose up -d db

down:        ## Stop the database containers
	docker compose down

sync-local:  ## Sync takeout zips from ./data into Postgres (bronze + silver)
	uv run pipeline sync --source local --path data

sync-drive:  ## Sync takeout zips from Google Drive into Postgres (needs SA access)
	uv run pipeline sync --source drive

test:        ## Run the E2E test suite (synthetic fixture + throwaway Postgres)
	uv run pytest -q

typecheck:   ## Run mypy over the pipeline package
	uv run mypy