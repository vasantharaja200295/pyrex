# 🔥 Pyrex

**Write Python. Get a fast web app. No Node.js. No React. No bundler.**

Pyrex is a Python JSX framework where you write components in `.px` files using familiar JSX-like syntax. The framework transpiles them into a single HTML page with Alpine.js reactivity — nothing else ships to the browser.

---

## Why Pyrex

| Framework            | Problem                                                                    |
| -------------------- | -------------------------------------------------------------------------- |
| Next.js / Remix      | React-first, requires Node.js. Python devs leave their ecosystem entirely. |
| Reflex / Solara      | Server roundtrip for every state change. Feels sluggish.                   |
| FastHTML / HTMX      | No real component model. Not composable.                                   |
| Django + DRF + React | Two codebases, two languages, manual API wiring.                           |

Pyrex lets you write a component, call a Python function from inside it via `@server_action`, and have the whole thing work as a fast HTML page — in one language, one file, one server.

---

## Features

- **JSX in Python** — write `<div class="card">` directly in `.px` files, no triple-quoted strings
- **File-based routing** — `app/about/page.px` → `/about`, automatic
- **`useState` / `useEffect`** — Alpine.js reactivity, declared in Python syntax
- **`@server_action`** — async Python functions auto-registered as POST endpoints, callable directly from JSX event handlers
- **`@layout`** — shared wrapper rendered around every page
- **Tailwind CSS v4** — opt-in with one config line, Play CDN injected automatically
- **Static file serving** — `app/static/` → `/static`, `public/` → `/public`
- **Shared Python packages** — `lib/`, `utils/`, any folder at the project root is importable
- **Hot reload** — file watcher + WebSocket push, browser refreshes automatically on save
- **`.env` support** — `PORT`, `PYREX_MODE`, `PYREX_SECRET_KEY` read from `.env` / `.env.production`

---

## Requirements

- Python ≥ 3.11
- MongoDB (for the tasks demo) — or remove it and use any DB you like
- No Node.js, no npm, no bundler

---

## Getting Started

### 1. Clone and install

```bash
git clone <repo-url>
cd py-react
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
pip install pymongo            # only needed for the tasks demo
```

### 2. Configure environment

Copy `.env` and adjust as needed:

```bash
cp .env .env.local
```

`.env` defaults:

```env
PORT=3000
PYREX_MODE=development
PYREX_SECRET_KEY=change-me-in-production
MONGODB_URI=mongodb://localhost:27017
```

### 3. Start the dev server

```bash
pyrex serve
```

Or via `main.py` (same thing, reads the same `.env`):

```bash
python main.py
```

Open [http://localhost:3000](http://localhost:3000). The server hot-reloads on every `.px` save.

---

## Project Structure

```
py-react/
├── main.py              # entry point — Pyrex() config + app.run()
├── .env                 # environment variables (PORT, PYREX_MODE, …)
├── pyproject.toml       # dependencies + pyrex CLI registration
│
├── app/                 # all routes live here
│   ├── layout.px        # shared layout (Header, nav) — wraps every page
│   ├── page.px          # route: /
│   ├── about/
│   │   └── page.px      # route: /about
│   ├── actions/
│   │   └── page.px      # route: /actions  — @server_action demo
│   ├── tasks/
│   │   └── page.px      # route: /tasks    — full CRUD demo (MongoDB)
│   ├── profile/
│   │   └── me/
│   │       └── page.px  # route: /profile/me
│   └── static/          # served at /static  (images, fonts, …)
│
├── lib/                 # shared Python helpers, importable from any .px file
│   └── db.py            # MongoDB client + col() helper
│
├── public/              # served at /public  (favicon, robots.txt, …)
│
└── pyrex/               # the framework package (don't edit unless hacking on Pyrex itself)
    ├── __init__.py
    ├── cli.py
    ├── engine.py
    ├── px_loader.py
    ├── jsx_runtime.py
    ├── tui.py
    ├── transpiler/
    └── static/
        └── pyrex.js     # tiny browser runtime (~100 lines)
```

---

## Writing Pages

Every `page.px` file exports one `@page` component. That's the route entry point.

```python
# app/hello/page.px
from pyrex import page, useState

@page(title="Hello — Pyrex")
def Hello():
    count, setCount = useState(0)

    return (
        <div class="p-8">
            <h1 class="text-2xl font-bold">Count: {count}</h1>
            <button onClick={lambda: setCount(count + 1)}>+</button>
        </div>
    )
```

→ visit [http://localhost:3000/hello](http://localhost:3000/hello)

### `@page` options

```python
@page(title="My Page", favicon="/static/icon.png")
def MyPage():
    ...
```

---

## Shared Layout

`app/layout.px` wraps every page automatically. Define a `@layout` component and receive `children`:

```python
# app/layout.px
from pyrex import layout, component

@layout
def Layout(children=None):
    return (
        <div>
            <nav>...</nav>
            <main>{children}</main>
        </div>
    )
```

---

## Server Actions

`@server_action` turns an async Python function into a POST endpoint. A JavaScript proxy is auto-generated so you can call it directly from JSX event handlers.

```python
from pyrex import page, useState, server_action

@server_action
async def save_name(name: str):
    # runs on the Python server — use any library, ORM, DB
    return {"saved": name.upper()}

@page
def Form():
    name, setName   = useState("")
    result, setResult = useState("")

    async def handleSubmit():
        data = await save_name(name=name)
        setResult(data["saved"])

    return (
        <div>
            <input onInput={lambda e: setName(e.target.value)} value={name} />
            <button onClick={handleSubmit}>Save</button>
            <p>{result}</p>
        </div>
    )
```

Type annotations (`name: str`, `id: int`) are respected — pydantic validates incoming request data automatically.

---

## Styling

### Tailwind CSS (recommended)

Enable in `main.py`:

```python
from pyrex import Pyrex
app = Pyrex()
app.config(styling="tailwind")
```

Tailwind v4 Play CDN is injected automatically. Use any Tailwind class in your JSX.

### Global CSS

Drop `app/globals.css` — it is injected into every page automatically.

### Route-level CSS

Drop `app/your-route/style.css` — injected only into that route's page.

---

## Static Files

| Folder        | URL prefix | Purpose                                                |
| ------------- | ---------- | ------------------------------------------------------ |
| `app/static/` | `/static`  | Per-app assets (images, fonts, icons)                  |
| `public/`     | `/public`  | Project-root assets (favicon, `robots.txt`, OG images) |

```jsx
<img src="/static/logo.png" />
<img src="/public/og.png" />
```

---

## Shared Python Packages

Any Python package at the project root is importable from `.px` files:

```python
# lib/db.py
from pymongo import MongoClient
_client = MongoClient(...)
def col(name): return _client["mydb"][name]
```

```python
# app/tasks/page.px
from lib.db import col
_col = col("tasks")
```

---

## Environment Variables

| Variable           | Default                     | Description                                |
| ------------------ | --------------------------- | ------------------------------------------ |
| `PORT`             | `3000`                      | HTTP server port                           |
| `PYREX_MODE`       | `development`               | `development` or `production`              |
| `PYREX_SECRET_KEY` | —                           | CSRF token secret (required in production) |
| `MONGODB_URI`      | `mongodb://localhost:27017` | MongoDB connection string                  |

Pyrex loads `.env` on startup, then `.env.{mode}` (e.g. `.env.production`) on top of it.

---

## CLI Reference

```bash
# Start dev server (reads app/ directory, hot reload on)
pyrex serve

# Start server pointing at a different app directory
pyrex serve --dir src/app --port 8080

# Build a single page to HTML (no server)
pyrex build app/page.px
```

---

## Lifecycle Hooks

```python
# main.py
from pyrex import Pyrex
app = Pyrex()

@app.on_startup
async def connect():
    # open DB connections, warm caches, etc.
    pass

@app.on_shutdown
async def disconnect():
    # graceful cleanup
    pass
```

---

## Demos Included

| Route         | What it shows                                               |
| ------------- | ----------------------------------------------------------- |
| `/`           | Home page with counter (`useState`)                         |
| `/about`      | Static page                                                 |
| `/profile/me` | Nested route                                                |
| `/actions`    | `@server_action` returning JSON + HTML; in-memory todo list |
| `/tasks`      | Full CRUD with MongoDB — create, toggle, delete tasks       |

---

## Roadmap

| Phase         | Goal                                                          | Status  |
| ------------- | ------------------------------------------------------------- | ------- |
| 1 — POC       | Parser, transpiler, `useState`, CLI                           | ✅ Done |
| 2 — DX        | Hot reload, file routing, `@server_action`, `.env`, Tailwind  | ✅ Done |
| 3 — Framework | SSR data fetching, form handling, static export, auth helpers | 🔜 Next |

---
