# Transoria

Desktop app for novel translation, glossary extraction, and batch text replacement.

## Requirements

- Python 3.11+
- Node.js 18+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Setup & Start

### Backend

```bash
# Install dependencies
uv sync --extra dev

# Run tests
pytest
```

### Frontend (dev)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

### Frontend (build)

```bash
cd frontend
npm run build
```

## Environment

Set your LLM API key before running translation tasks:

```bash
export TRANSORIA_VOLCENGINE_ARK_API_KEY=your_key_here
```
