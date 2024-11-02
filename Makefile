all: report

report:
	@jq .weather_report -r weather_report.json | fmt 80

run:
	uv run foggybot.py

fmt:
	uv run --with ruff ruff format foggybot.py
