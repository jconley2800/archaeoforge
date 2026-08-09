.PHONY: install dev test lint demo clean

install:
	./install_linux.sh

dev:
	./install_linux.sh --dev

test:
	PYTHONPATH=src python3 -m pytest -q

lint:
	PYTHONPATH=src python3 -m ruff check src tests

demo:
	./run_babylon_demo.sh --skip-blender

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	rm -rf projects/babylon_570_bce/.archaeoforge projects/babylon_570_bce/outputs
