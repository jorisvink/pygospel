# vigil-python Makefile

OBJDIR?=obj
BUILD=build/host
KYRKA=$(BUILD)/libkyrka
PYMOD=$(KYRKA)/obj/python
PYTHON?=python3

DESTDIR?=

PYSITE?=$(shell $(PYTHON) -c \
    'import sysconfig; print(sysconfig.get_path("purelib"))')

SRC=	src/pygospel.py
TESTS=	tests/gospel_test.py tests/agent_test.py
TOOLS=	examples/chatter.py examples/echo.py examples/presence.py \
	examples/agent.py tools/transcribe.py tools/render.py

all: python-mod

python-mod: dist-build/patches/libkyrka-python.patch
	@./dist-build/deps.sh
	@./dist-build/libkyrka.sh

install: all
	mkdir -p $(DESTDIR)$(PYSITE)
	install -m 644 $(SRC) $(DESTDIR)$(PYSITE)
	install -m 555 $(PYMOD)/libkyrka.so $(DESTDIR)$(PYSITE)

test:
	@for t in $(TESTS); do $(PYTHON) $$t || exit 1; done

smoke: python-mod
	@PYTHONPATH=$(PYMOD):src $(PYTHON) tests/smoke.py

lint:
	@$(PYTHON) -m py_compile $(SRC) $(TESTS) tests/fakekyrka.py \
	    tests/smoke.py $(TOOLS)
	@$(PYTHON) tools/check-style.py $(SRC) $(TESTS) tests/fakekyrka.py \
	    tests/smoke.py $(TOOLS)
	@echo "python ok"

cathedral:
	@cd tools/cathedral && ./cathedral.sh up

cathedral-down:
	@cd tools/cathedral && ./cathedral.sh down

cathedral-status:
	@cd tools/cathedral && ./cathedral.sh status || true

cathedral-e2e: python-mod
	@cd tools/cathedral && ./cathedral.sh e2e

clean:
	rm -rf $(OBJDIR) build/host build/cathedral
	find . -name __pycache__ -type d -exec rm -rf {} + 2> /dev/null || true

dist-clean: clean
	rm -rf build

.PHONY: all python-mod test smoke lint install cathedral cathedral-down
.PHONY: cathedral-status cathedral-e2e clean dist-clean
