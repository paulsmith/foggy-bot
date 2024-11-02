all:
	uv run foggybot.py

fmt:
	uv run --with ruff ruff format foggybot.py
