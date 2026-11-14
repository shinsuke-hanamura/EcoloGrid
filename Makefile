DC = docker-compose

.PHONY: build up down test cli cli-check bot shell logs venv play
build:  ; $(DC) build
up:     ; $(DC) up -d server
down:   ; $(DC) down
logs:   ; $(DC) logs -f server
test:   ; $(DC) run --rm --no-deps dev pytest -q
cli:    ; $(DC) run --rm dev python -m ecologrid_cli play --name $${NAME:-player}
cli-check: ; $(DC) run --rm dev python -m ecologrid_cli --server http://server:8000 check
bot:    ; $(DC) run --rm dev python -m ecologrid_cli bot --name $${NAME:-bot} --seconds $${SECONDS_:-30}
shell:  ; $(DC) run --rm dev bash

# --- Docker なしの CLI(T640 上の .venv。サーバは Docker のまま localhost:8000) ---
venv:   ; python3 -m venv --without-pip .venv && mkdir -p /tmp/getpip-dl && curl -fsSL -o /tmp/getpip-dl/get-pip.py https://bootstrap.pypa.io/pip/3.8/get-pip.py && .venv/bin/python -I /tmp/getpip-dl/get-pip.py -q && .venv/bin/pip install -q -r cli/requirements.txt
play:   ; PYTHONPATH=cli .venv/bin/python -m ecologrid_cli play --name $${NAME:-player}
