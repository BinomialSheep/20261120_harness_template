PYTHON ?= python3
.PHONY: help test demo doctor render docs-check beads-check
help:
	@echo 'test, demo, doctor, render, docs-check, beads-check; set PYTHON to your Python 3.11+ executable'
test:
	$(PYTHON) tools/test.py --strict --record
demo:
	$(PYTHON) tools/demo.py
doctor:
	$(PYTHON) -m harness doctor
render:
	$(PYTHON) -m harness render
docs-check:
	$(PYTHON) tools/check_docs.py
beads-check:
	$(PYTHON) integrations/beads/acceptance.py
