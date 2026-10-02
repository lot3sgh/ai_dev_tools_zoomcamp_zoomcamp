.PHONY: up down sync-local sync-drive test test-unit typecheck image sync-drive-container chat chat-up eval-gate backup install-timers uninstall-timers

up:          ## Bring up the Postgres database (docker compose)
	docker compose up -d db

down:        ## Stop the database containers
	docker compose down

sync-local:  ## Sync takeout zips from ./data into Postgres (bronze + silver)
	uv run pipeline sync --source local --path data

sync-drive:  ## Sync takeout zips from Google Drive into Postgres (needs SA access)
	uv run pipeline sync --source drive

image:       ## Build the containerized sync runner (Linux deployment)
	docker compose build sync

sync-drive-container:  ## Run a Drive sync through the container (same engine, once)
	docker compose --profile sync run --rm sync

chat:       ## Build the Health Assistant chat image (bakes the generic core, ADR-0006)
	docker compose build chat

chat-up:    ## Bring the chat service up on the LAN (with the rest of the stack)
	docker compose up -d chat

eval-gate:  ## Eval-corpus merge gate (skips cleanly without LLM_API_KEY)
	bash deploy/eval-corpus.sh

backup:     ## Take a pg_dump now (deploy/backup.sh; optional off-box push)
	bash deploy/backup.sh

# --- Linux host only: systemd timers (Phase 1). The Makefile refuses on macOS on purpose. ---
install-timers:  ## Install the daily sync + nightly backup timers on the Linux host
	@test "$$(uname -s)" = Linux || { echo "install-timers targets the Linux deployment host; refusing on macOS" >&2; exit 1; }
	install -m 0644 deploy/health-sync.timer deploy/health-sync.service deploy/health-backup.timer deploy/health-backup.service /etc/systemd/system/
	systemctl daemon-reload
	systemctl enable --now health-sync.timer health-backup.timer
	@echo "timers installed: health-sync.timer (06:15 daily), health-backup.timer (03:10 daily)"

uninstall-timers:  ## Remove the systemd timers from the Linux host
	@test "$$(uname -s)" = Linux || { echo "uninstall-timers targets the Linux deployment host; refusing on macOS" >&2; exit 1; }
	systemctl disable --now health-sync.timer health-backup.timer
	rm -f /etc/systemd/system/health-sync.service /etc/systemd/system/health-sync.timer \
	      /etc/systemd/system/health-backup.service /etc/systemd/system/health-backup.timer
	systemctl daemon-reload
	@echo "timers removed"

test:        ## Run the E2E suite (integration, throwaway Postgres) + frontend tests
	uv run pytest -q
	cd frontend && node --test tests/*.test.mjs

test-unit:   ## Unit-only backend run (no database): -m "not integration"
	uv run pytest -m "not integration" -q

typecheck:   ## Run mypy over the pipeline package
	uv run mypy