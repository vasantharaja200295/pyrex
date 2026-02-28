# Pyrex

**Write Python. Get a reactive web app. No Node.js. No React. No bundler.**

Pyrex is a Python JSX framework where you write components in `.px` files using JSX syntax directly inside Python functions. The framework transpiles them into HTML pages powered by [Alpine.js](https://alpinejs.dev) — a minimal (~15 kB) declarative UI library. No React, no bundler, no build step.

---

## Table of Contents

1. [Why Pyrex](#why-pyrex)
2. [How It Works](#how-it-works)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Project Structure](#project-structure)
6. [Writing Pages](#writing-pages)
7. [Components](#components)
8. [Shared Layout](#shared-layout)
9. [Hooks](#hooks)
   - [useState](#usestate)
   - [useEffect](#useeffect)
   - [useRef](#useref)
   - [createStore / useStore / useSelector](#createstore--usestore--useselector)
   - [js()](#js)
10. [Server Actions](#server-actions)
11. [Routing](#routing)
12. [Styling](#styling)
13. [Static Files](#static-files)
14. [Environment Variables](#environment-variables)
15. [Application Config & Lifecycle](#application-config--lifecycle)
16. [CLI Reference](#cli-reference)
17. [Demos Included](#demos-included)
18. [Syntax: Pyrex vs React](#syntax-pyrex-vs-react)
19. [Drawbacks & Limitations](#drawbacks--limitations)
20. [Roadmap](#roadmap)

---

## Why Pyrex

| Framework            | Problem                                                                    |
| -------------------- | -------------------------------------------------------------------------- |
| Next.js / Remix      | React-first, requires Node.js. Python devs leave their ecosystem entirely. |
| Reflex / Solara      | Server roundtrip for every state change. Feels sluggish.                   |
| FastHTML / HTMX      | No real component model. Not composable.                                   |
| Django + DRF + React | Two codebases, two languages, manual API wiring.                           |

Pyrex lets you define a component, call a Python function from inside it via `@server_action`, and ship a fast reactive HTML page — in one language, one file, one server.

---

## How It Works

```
page.px
  │
  ▼  px_loader.py      Load .px file, run AST transformer
  │                    (rewrites JSX + hooks into valid Python)
  ▼  pyrex/__init__.py  useState / useRef / useStore etc.
  │                    register state/handlers into context vars
  ▼  transpiler.py     Walk JSX tree → Alpine HTML
  │                    StateVar  → <span x-text="…">
  │                    Lambda    → Alpine x-data method
  │                    ListComp  → <template x-for>
  ▼
page.html  (HTML + Alpine.js CDN + inline x-data directives)
```

Each `.px` file is valid Python with JSX embedded. The framework's custom loader runs an AST transformer over the file before execution, then the transpiler converts the resulting JSX tree into Alpine.js HTML.

---

## Requirements

- Python ≥ 3.11
- No Node.js, no npm, no bundler

---

## Installation

```bash
git clone <repo-url>
cd py-react
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

For the MongoDB tasks demo only:

```bash
pip install pymongo
```

---

## Project Structure

```
my-app/
├── main.py              # entry point — Pyrex() config + app.run()
├── .env                 # environment variables (PORT, PYREX_MODE, …)
├── pyproject.toml
│
├── app/                 # all routes live here
│   ├── layout.px        # shared layout — wraps every page
│   ├── page.px          # route: /
│   ├── about/
│   │   └── page.px      # route: /about
│   ├── profile/
│   │   └── me/
│   │       └── page.px  # route: /profile/me
│   └── static/          # served at /static
│
├── lib/                 # shared Python helpers, importable from any .px file
├── public/              # served at /public (favicon, robots.txt, …)
│
└── pyrex/               # the framework package
```

---

## Writing Pages

Every `page.px` exports one `@page` component — the route entry point.

```python
# app/hello/page.px
from pyrex import page, useState

@page(title="Hello — Pyrex")
def Hello():
    count, setCount = useState(0)

    return (
        <div class="p-8">
            <h1>Count: {count}</h1>
            <button onClick={lambda: setCount(count + 1)}>+</button>
            <button onClick={lambda: setCount(count - 1)}>-</button>
        </div>
    )
```

Visit `http://localhost:3000/hello`.

### `@page` options

```python
@page(
    title="My Page",
    favicon="/static/icon.png",
    meta={
        "description": "Page description for SEO",
        "og:title": "My Page",
    },
)
def MyPage():
    ...
```

| Option    | Type   | Description                        |
| --------- | ------ | ---------------------------------- |
| `title`   | `str`  | `<title>` tag content              |
| `favicon` | `str`  | Path to favicon (e.g. `/static/…`) |
| `meta`    | `dict` | Key/value `<meta>` tags            |

---

## Components

Use `@component` to define reusable components. Reference them with uppercase tags in JSX.

```python
from pyrex import component, useState

@component
def Card():
    open, setOpen = useState(False)

    return (
        <div class="card">
            <button onClick={lambda: setOpen(not open)}>Toggle</button>
            <p>{open}</p>
        </div>
    )

@page
def Home():
    return (
        <main>
            <Card />
            <Card />
        </main>
    )
```

---

## Shared Layout

`app/layout.px` wraps every page automatically. Define a `@layout` component with a `children` parameter:

```python
# app/layout.px
from pyrex import layout, component

@component
def Nav():
    return (
        <nav>
            <a href="/">Home</a>
            <a href="/about">About</a>
        </nav>
    )

@layout
def Layout(children=None):
    return (
        <div>
            <Nav />
            <main>{children}</main>
            <footer>Powered by Pyrex</footer>
        </div>
    )
```

---

## Hooks

### useState

Declare a reactive state variable. Returns a `(value, setter)` tuple.

```python
count, setCount = useState(0)
name,  setName  = useState("")
items, setItems = useState([])
```

Use the value in JSX — it automatically becomes reactive:

```python
return (
    <div>
        <p>Count: {count}</p>
        <button onClick={lambda: setCount(count + 1)}>+</button>
    </div>
)
```

State can be initialised with data fetched server-side at render time:

```python
@page
def Tasks():
    tasks, setTasks = useState(fetch_all_tasks())   # runs on the server
    ...
```

### useEffect

Register a client-side effect. Currently no-op — emits no browser code. Full effect support (watching state changes) is planned for Phase 3.

```python
useEffect(())   # placeholder for Alpine-level watchers
```

### useRef

Create a reference to a DOM element. Access it as `$refs.name` inside handlers.

```python
inputRef = useRef()

async def focusInput():
    js("$refs.inputRef.focus()")

return (
    <div>
        <input ref={inputRef} />
        <button onClick={focusInput}>Focus</button>
    </div>
)
```

### createStore / useStore / useSelector

Global reactive state that persists across components and survives re-renders. Backed by [Alpine.store](https://alpinejs.dev/globals/alpine-store/).

**Define a store** (typically in a separate file):

```python
# store/cart.py
from pyrex import createStore

cartStore = createStore("cart", {
    "items": [],
    "count": 0,
    "total": 0.0,
})
```

**Access the full store** in a component:

```python
from pyrex import component, useStore
from store.cart import cartStore

@component
def CartBadge():
    cart = useStore(cartStore)

    return <span>{cart.count} items</span>
    # → <span x-text="$store.cart.count"></span>
```

**Select a single slice** with `useSelector`:

```python
from pyrex import component, useSelector
from store.cart import cartStore

@component
def CartTotal():
    total = useSelector(cartStore, lambda s: s.total)

    return <span>Total: ${total}</span>
```

### js()

Inject raw JavaScript — either as a statement inside a handler or as an inline `<script>` in JSX.

```python
# Inside a handler — emitted verbatim into the Alpine method
async def handleScroll():
    js("window.scrollTo(0, 0)")

# Inside JSX — emitted as <script>…</script>
return (
    <div>
        {js("console.log('rendered')")}
    </div>
)
```

Alpine magic variables (`$refs`, `$store`, `$el`, `$dispatch`, etc.) work inside `js()` strings.

---

## Server Actions

`@server_action` turns an async Python function into a POST endpoint. A JavaScript proxy is auto-generated so you can call it from JSX handlers like a regular async function.

```python
from pyrex import page, useState, server_action

@server_action
async def greet(name: str, language: str):
    phrases = {"english": "Hello", "spanish": "Hola", "french": "Bonjour"}
    return {"greeting": f"{phrases.get(language, 'Hello')}, {name}!"}

@page
def Form():
    name,   setName   = useState("")
    result, setResult = useState("")

    async def handleSubmit():
        data = await greet(name=name, language="english")
        setResult(data["greeting"])

    return (
        <div>
            <input onInput={lambda e: setName(e.target.value)} value={name} />
            <button onClick={handleSubmit}>Greet</button>
            <p>{result}</p>
        </div>
    )
```

**How it works:**
- The function runs on the Python server (can use any library, ORM, database).
- Parameters annotated with `str`, `int`, `float`, `bool` are validated from the POST body.
- Return values are JSON-serialised or passed back as an HTML string.
- The auto-generated JS proxy calls `POST /__pyrex/` under the hood.

**Server actions can return:**
- A Python `dict` → serialised to JSON, accessible as a JS object.
- A `str` → treated as raw HTML and can be written directly into `innerHTML`.
- A `list` → serialised to a JSON array.

---

## Routing

Routing is file-based and automatic. Each `page.px` inside `app/` maps to a URL:

| File                       | Route        |
| -------------------------- | ------------ |
| `app/page.px`              | `/`          |
| `app/about/page.px`        | `/about`     |
| `app/profile/me/page.px`   | `/profile/me`|

No router configuration required.

---

## Styling

### Tailwind CSS (recommended)

Enable in `main.py`:

```python
from pyrex import Pyrex
app = Pyrex()
app.config(styling="tailwind")
```

Tailwind v4 Play CDN is injected automatically. Use any Tailwind utility class:

```python
return <div class="flex items-center gap-4 p-6 bg-white rounded-xl shadow">...</div>
```

### Google Fonts

Pass font names to `app.config()`:

```python
# Simple — loads weights 400 and 700
app.config(styling="tailwind", google_fonts=["Inter", "Manrope"])

# Custom weights per font
app.config(google_fonts={"Inter": [400, 600, 700], "Roboto Mono": [400]})
```

When combined with `styling="tailwind"`, CSS variables (e.g. `font-inter`) are injected automatically into `@theme`.

### Global CSS

Drop `app/globals.css` — injected into every page.

### Route-level CSS

Drop `app/your-route/style.css` — injected only into that route's page.

### Inline styles

Standard HTML inline styles always work:

```python
return <div style="color:red;font-weight:bold;">Hello</div>
```

---

## Static Files

| Folder        | URL prefix | Purpose                             |
| ------------- | ---------- | ----------------------------------- |
| `app/static/` | `/static`  | Per-app assets (images, fonts, …)   |
| `public/`     | `/public`  | Project-root assets (favicon, OG …) |

```python
return (
    <div>
        <img src="/static/logo.png" />
        <img src="/public/og.png" />
    </div>
)
```

---

## Environment Variables

Pyrex loads `.env` on startup, then `.env.{mode}` (e.g. `.env.production`) on top of it.

| Variable           | Default                     | Description                              |
| ------------------ | --------------------------- | ---------------------------------------- |
| `PORT`             | `3000`                      | HTTP server port                         |
| `PYREX_MODE`       | `development`               | `development` or `production`            |
| `PYREX_SECRET_KEY` | —                           | CSRF token secret (set in production)    |
| `MONGODB_URI`      | `mongodb://localhost:27017` | MongoDB connection string (demo only)    |

---

## Application Config & Lifecycle

```python
# main.py
from pyrex import Pyrex

app = Pyrex()
app.config(styling="tailwind", google_fonts=["Inter"])

@app.on_startup
async def connect():
    # open DB connections, warm caches, etc.
    pass

@app.on_shutdown
async def disconnect():
    # graceful cleanup
    pass

if __name__ == "__main__":
    app.run()   # reads PORT, PYREX_MODE from .env
```

`app.run()` accepts keyword overrides: `port=`, `directory=`, `watch=`, `mode=`.

---

## CLI Reference

```bash
# Start dev server with hot reload (reads app/ directory)
pyrex serve

# Custom directory and port
pyrex serve --dir src/app --port 8080

# Build a single page to static HTML (no server needed)
pyrex build app/page.px
```

The dev server hot-reloads on every `.px` save — the browser refreshes automatically.

---

## Demos Included

| Route         | What it shows                                          |
| ------------- | ------------------------------------------------------ |
| `/`           | Counter (`useState`) + controlled input                |
| `/about`      | In-memory todo list with list comprehension rendering  |
| `/profile/me` | Nested route                                           |
| `/actions`    | `@server_action` — JSON response + HTML string response|
| `/tasks`      | Full MongoDB CRUD — create, toggle, delete tasks       |

---

## Syntax: Pyrex vs React

This section shows how familiar React patterns map to Pyrex equivalents.

### File extension

| React         | Pyrex       |
| ------------- | ----------- |
| `page.jsx`    | `page.px`   |
| `page.tsx`    | `page.px`   |

### Imports

```python
# React
import React, { useState, useEffect, useRef } from 'react'

# Pyrex
from pyrex import page, component, useState, useEffect, useRef
```

### Component definition

```python
# React
export default function Counter() { ... }

# Pyrex
@page
def Counter(): ...

# Reusable component
@component
def Card(): ...
```

### JSX return syntax

```python
# React — JSX in JS, no parentheses required
return (
  <div className="card">Hello</div>
)

# Pyrex — JSX directly in Python return, parentheses required
return (
    <div class="card">Hello</div>
)
```

### `class` vs `className`

```python
# React — must use className (JS reserved word)
<div className="card flex items-center">

# Pyrex — use class (standard HTML attribute)
<div class="card flex items-center">
```

> `className` also works in Pyrex and is mapped to `class` by the transpiler.

### State

```python
# React
const [count, setCount] = useState(0)

# Pyrex
count, setCount = useState(0)
```

### Event handlers — inline

```python
# React
<button onClick={() => setCount(count + 1)}>+</button>

# Pyrex
<button onClick={lambda: setCount(count + 1)}>+</button>
```

### Event handlers — with event object

```python
# React
<input onChange={(e) => setName(e.target.value)} />

# Pyrex
<input onInput={lambda e: setName(e.target.value)} />
```

### Event handlers — async functions

```python
# React
async function handleSubmit() {
  const data = await saveUser(name)
  setResult(data.message)
}
<button onClick={handleSubmit}>Save</button>

# Pyrex — define async def inside the component, reference directly
async def handleSubmit():
    data = await save_user(name=name)
    setResult(data["message"])

return <button onClick={handleSubmit}>Save</button>
```

### List rendering

```python
# React
{items.map(item => (
  <li key={item.id}>{item.title}</li>
))}

# Pyrex — Python list comprehension inside JSX
{[<li>{item.title}</li> for item in items]}
```

### Conditional rendering

```python
# React
{isOpen && <Modal />}
{isOpen ? <Modal /> : <Placeholder />}

# Pyrex — use Alpine.js x-show, or raw JS in onclick strings
# Option 1: x-show attribute (Alpine native)
<div x-show="isOpen"><Modal /></div>

# Option 2: pass the flag as a prop / JS ternary inline
# There is no Python if/else conditional rendering in JSX yet.
```

### API calls / server functions

```python
# React — needs a separate API route + fetch()
const res = await fetch('/api/save', { method: 'POST', body: JSON.stringify({ name }) })
const data = await res.json()

# Pyrex — @server_action, called like a regular async function
@server_action
async def save_name(name: str):
    return {"saved": name.upper()}

# Inside the component handler:
async def handleSave():
    data = await save_name(name=name)
    setResult(data["saved"])
```

### Layout / wrapper

```python
# React (Next.js) — app/layout.tsx
export default function RootLayout({ children }) {
  return <html><body>{children}</body></html>
}

# Pyrex — app/layout.px
@layout
def Layout(children=None):
    return (
        <div>
            <nav>...</nav>
            <main>{children}</main>
        </div>
    )
```

### Raw JavaScript escape hatch

```python
# React — dangerouslySetInnerHTML or useEffect with direct DOM access
useEffect(() => { window.scrollTo(0, 0) }, [])

# Pyrex — js() function
async def handleClick():
    js("window.scrollTo(0, 0)")
```

### DOM refs

```python
# React
const inputRef = useRef(null)
inputRef.current.focus()

# Pyrex
inputRef = useRef()

async def handleFocus():
    js("$refs.inputRef.focus()")

return <input ref={inputRef} />
```

### Global state

```python
# React (Zustand-like)
const cartStore = create(set => ({ count: 0, add: () => set(s => ({ count: s.count + 1 })) }))
const count = useStore(cartStore, s => s.count)

# Pyrex
cartStore = createStore("cart", {"count": 0})
count = useSelector(cartStore, lambda s: s.count)
```

---

## Drawbacks & Limitations

Pyrex is a v0.1–v0.2 framework. Be aware of the following before using it in production.

### Alpine.js dependency in the browser

Pyrex's output is not zero-dependency HTML. Each page loads Alpine.js from a CDN. While Alpine is tiny (~15 kB gzipped), it is still an external dependency. A pure offline environment will not work without the CDN.

### No true SSR hydration

Pages render initial state on the server (including server-side data in `useState`), but reactivity is handled entirely by Alpine.js in the browser. There is no incremental hydration or streaming — the full page is delivered at once.

### No conditional rendering in Python

You cannot write `if condition: return <A /> else return <B />` in JSX. Use Alpine.js `x-show`, CSS `display:none`, or JavaScript ternary patterns instead. Python `if/else` for component branching is not yet supported.

### `useEffect` is currently limited

`useEffect` is registered but does not yet emit Alpine watchers. It is effectively a no-op. Full effect support (watching state changes, cleanup) is planned for Phase 3.

### Hot reload is a full page refresh

The dev server watches `.px` files and triggers a browser refresh via WebSocket. It is not component-level HMR — the whole page reloads on every save.

### No TypeScript or prop types

`.px` files are plain Python. There is no static type-checking for JSX props, component interfaces, or hook return types. Errors in prop types are usually discovered at runtime.

### Props accept only basic Python values

Component props work for simple scalar values (strings, numbers, booleans). Passing complex Python objects (custom classes, nested dicts) as JSX props may not work as expected. Pass primitive values; fetch complex data inside the component.

### No CSS modules or scoped styles

All styles are global. Use Tailwind utility classes, inline styles, or a global stylesheet. There is no per-component style scoping.

### Python 3.11+ required

The framework depends on Python 3.11 features (`ast.unparse`, union type hints `X | Y`). Earlier versions are not supported.

### No component library ecosystem

There are no third-party Pyrex component libraries. You build your own components or bring in plain HTML/CSS UI kits styled manually.

### List comprehensions with complex JSX can hit edge cases

The JSX transformer handles `{[<el /> for x in xs]}` but deeply nested or multi-expression comprehensions may produce unexpected output. Keep list comprehensions simple.

### Raw JS is sometimes required

Complex DOM interactions that do not fit the Alpine.js model (e.g. Canvas API, WebGL, third-party JS libraries) require the `js()` escape hatch or plain `onclick="..."` attribute strings. There is no pure-Python path to arbitrary browser APIs.

---

## Roadmap

| Phase         | Goal                                                          | Status      |
| ------------- | ------------------------------------------------------------- | ----------- |
| 1 — POC       | Parser, transpiler, `useState`, CLI                           | Done        |
| 2 — DX        | Hot reload, file routing, `@server_action`, `.env`, Tailwind  | Done        |
| 3 — Framework | SSR data fetching, form handling, static export, auth helpers | Next        |
| 4 — Go Port   | Entire parser + transpiler ported to Go — sub-ms build times  | Planned     |

The browser output format (HTML + Alpine.js directives) is stable across all phases. Only the server runtime that generates it changes between phases.

---

_Pyrex — Python JSX Framework — v0.2_