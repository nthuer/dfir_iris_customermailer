.PHONY: help test build install clean

PYTHON ?= python3

help:
	@echo "make test     - run the unit tests (no IRIS required)"
	@echo "make build    - build the wheel into ./dist"
	@echo "make install  - build and install into the IRIS containers (worker + app)"
	@echo "make clean    - remove build artefacts"

test:
	$(PYTHON) -m pytest

build: clean
	$(PYTHON) -m build --wheel || $(PYTHON) setup.py bdist_wheel

install:
	./buildnpush2iris.sh -a

clean:
	rm -rf dist build *.egg-info .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
