# Talky.ai Backend — common tasks.
#
# Requires pip-tools installed:  pip install pip-tools==7.4.1
# (already in requirements-dev.txt)
#
# Usage examples:
#     make lock              # refresh requirements.txt from requirements.in
#     make lock-upgrade      # bump every pin to latest compatible version
#     make sync              # install exactly what's in the lock (CI/prod parity)
#     make verify-lock       # CI: fail if requirements.in has drifted from .txt

PIP_COMPILE := pip-compile --resolver=backtracking --strip-extras --generate-hashes
PIP_SYNC := pip-sync

.PHONY: help lock lock-dev lock-all lock-upgrade sync verify-lock

help:
	@echo "Targets:"
	@echo "  lock           Regenerate requirements.txt from requirements.in"
	@echo "  lock-dev       Regenerate requirements-dev.txt from requirements-dev.in"
	@echo "  lock-all       Both of the above"
	@echo "  lock-upgrade   Bump all pins to latest compatible versions"
	@echo "  sync           Install exactly what's pinned (drops anything else)"
	@echo "  verify-lock    Fail if .in files have drifted from the locks (CI)"

lock:
	$(PIP_COMPILE) requirements.in -o requirements.txt

lock-dev:
	$(PIP_COMPILE) requirements-dev.in -o requirements-dev.txt

lock-all: lock lock-dev

lock-upgrade:
	$(PIP_COMPILE) --upgrade requirements.in -o requirements.txt
	$(PIP_COMPILE) --upgrade requirements-dev.in -o requirements-dev.txt

sync:
	$(PIP_SYNC) requirements.txt requirements-dev.txt

# Used in CI — generates a fresh lock to a temp file and diffs against
# the committed lock. Any drift means someone edited requirements.txt
# directly instead of regenerating it from requirements.in.
verify-lock:
	@$(PIP_COMPILE) --quiet requirements.in -o /tmp/req.lock.check
	@diff -u requirements.txt /tmp/req.lock.check || \
	  (echo ""; echo "ERROR: requirements.txt is out of sync with requirements.in."; \
	   echo "Run 'make lock' and commit the result."; exit 1)
