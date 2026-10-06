.PHONY: install dev run test lint format
install: ; pip install -r requirements.txt
dev:     ; pip install -r requirements-dev.txt
run:     ; streamlit run app.py
test:    ; DIVITRACK_OFFLINE=1 pytest -q --cov=src/divitrack
lint:    ; ruff check src tests app.py && ruff format --check src tests app.py && mypy src
format:  ; ruff format src tests app.py && ruff check --fix src tests app.py
