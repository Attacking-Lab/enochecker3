uv_flags := env_var_or_default("UV_FLAGS", "--inexact")
uv_run := "env VIRTUAL_ENV=.venv uv run " + uv_flags

test_flags := env_var_or_default("TEST_FLAGS", "")

# Image built from .woodpecker/Dockerfile for the Woodpecker test.yml pipeline.
ci_image := env_var_or_default("CI_IMAGE", "oci.atklab.cloud/enochecker3/enochecker3-ci")
ci_image_tag := env_var_or_default("CI_IMAGE_TAG", "latest")
ci_image_ref := ci_image + ":" + ci_image_tag

[private]
default:
    @just --list

# Run the full check pipeline (format, lint, type checks, tests)
[group('dev')]
all: format lint mypy ty test

# Auto-fix formatting and lint issues
[group('dev')]
fix: format-fix lint-fix

[group('dev')]
format *args:
    @{{ uv_run }} ruff format --check {{ args }}

[group('dev')]
format-fix *args:
    @{{ uv_run }} ruff format {{ args }}

[group('dev')]
lint *args:
    @{{ uv_run }} ruff check {{ args }}

[group('dev')]
lint-fix *args:
    @{{ uv_run }} ruff check --fix {{ args }}

[group('dev')]
mypy *args:
    @{{ uv_run }} mypy src/enochecker3/ {{ args }}

[group('dev')]
ty *args:
    @{{ uv_run }} ty check src/enochecker3/ {{ args }}

[group('dev')]
sync *args:
    @uv sync {{ uv_flags }} {{ args }}

# Run the full test suite under coverage
[group('test')]
test *args:
    @test -z "$(ls tests 2>/dev/null)" || ( \
    	{{ uv_run }} coverage run -m pytest -W error -v {{ test_flags }} {{ args }} && \
    	{{ uv_run }} coverage report -m \
    )

# Run unit tests only (optionally a -k filter or path)
[group('test')]
test-unit filter='':
    @{{ uv_run }} pytest -W error -v -m 'not e2e and not mongodb' {{ filter }}

# Run end-to-end checker/service round-trip tests
[group('test')]
test-e2e filter='':
    @{{ uv_run }} pytest -W error -v -m e2e {{ filter }}

# Run tests that require a real MongoDB (needs MONGO_HOST)
[group('test')]
test-mongodb filter='':
    @{{ uv_run }} pytest -W error -v --with_mongodb -m mongodb {{ filter }}

[group('build')]
build *args:
    @uv build {{ args }}

# Build the Woodpecker CI runner image
[group('release')]
ci-image-build *args:
    docker build -f .woodpecker/Dockerfile -t {{ ci_image_ref }} {{ args }} .

# Build and push the Woodpecker CI runner image
[group('release')]
ci-image-push: ci-image-build
    docker push {{ ci_image_ref }}
