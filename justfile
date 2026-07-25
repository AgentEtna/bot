# run phi
dev:
    uv run uvicorn src.bot.main:app --reload

run:
    uv run uvicorn src.bot.main:app

# testing
test:
    uv run pytest tests/ -v

evals:
    uv run pytest evals/ -v

# deployment — this is the only path; there is no deploy CI
deploy:
    flyctl deploy

# code quality
fmt:
    uv run ruff format src/ evals/ tests/

lint:
    uv run ruff check src/ evals/ tests/

typecheck:
    uv run ty check src/ evals/ tests/

# loq — relax line limits for files that legitimately grew
loq-relax +files:
    uvx loq relax {{files}}

check: lint typecheck test

# setup reference projects
setup:
    @mkdir -p .eggs
    @[ -d .eggs/void ] || git clone https://tangled.sh/@cameron.pfiffer.org/void.git .eggs/void
    @[ -d .eggs/penelope ] || git clone https://github.com/haileyok/penelope.git .eggs/penelope
    @[ -d .eggs/marvin ] || git clone https://github.com/PrefectHQ/marvin.git .eggs/marvin
