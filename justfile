uv_flags := env_var_or_default("UV_FLAGS", "--inexact")
uv_run := "env VIRTUAL_ENV=.venv uv run " + uv_flags

test_flags := env_var_or_default("TEST_FLAGS", "")

all: format lint mypy ty test

fix: format-fix lint-fix

format:
	@{{uv_run}} ruff format --check

format-fix:
	@{{uv_run}} ruff format

lint:
	@{{uv_run}} ruff check

lint-fix:
	@{{uv_run}} ruff check --fix

mypy:
	@{{uv_run}} mypy src/enochecker3/

ty:
	@{{uv_run}} ty check src/enochecker3/

build:
	@uv build

test:
	@test -z "$(ls tests 2>/dev/null)" || ( \
		{{uv_run}} coverage run -m pytest -W error -v {{test_flags}} && \
		{{uv_run}} coverage report -m \
	)

sync:
	@uv sync {{uv_flags}}
