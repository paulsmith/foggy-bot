all: report

report:
	@jq .weather_report -r weather_report.json | fmt 80

run:
	uv run foggybot.py

fmt:
	uvx ruff format foggybot.py
