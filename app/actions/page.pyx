from pyrex import page, server_action

# ── Server actions ─────────────────────────────────────────────────────────────
#
# Each @server_action is registered as:
#   POST /__pyrex/actions/<name>   (JSON body, JSON response)
#
# A named JS proxy function is auto-generated in every page:
#   async function get_greeting(name, language) { ... }
#   async function add_todo(text) { ... }
#   async function clear_todos() { ... }
#
# Return values are serialised with JSONResponse:
#   - dict / list → JSON object / array
#   - str         → JSON string (assign to innerHTML on the client)


# ── 1. Stateless action — returns JSON dict ───────────────────────────────────

@server_action
async def get_greeting(name: str, language: str):
    """Compute a localised greeting — returns a JSON dict."""
    phrases = {
        "english":  "Hello",
        "spanish":  "Hola",
        "french":   "Bonjour",
        "german":   "Hallo",
        "japanese": "Konnichiwa",
        "arabic":   "Marhaba",
    }
    greeting = phrases.get(language.lower(), "Hello")
    return {"greeting": f"{greeting}, {name}!", "language": language.capitalize()}


# ── 2. Stateful actions — return HTML strings (JSON-encoded by the server) ────
#    (state resets on server restart or when this file is hot-reloaded)

_todos: list[str] = []


@server_action
async def add_todo(text: str):
    """Append a todo item and return the updated list as an HTML string."""
    if text.strip():
        _todos.append(text.strip())
    if not _todos:
        return '<p style="color:#94a3b8;margin:0">No items yet.</p>'
    items = "".join(
        f'<li style="padding:.4rem 0;border-bottom:1px solid #f1f5f9;color:#334155">'
        f'{i + 1}. {item}</li>'
        for i, item in enumerate(_todos)
    )
    return f'<ul style="margin:0;padding:0;list-style:none">{items}</ul>'


@server_action
async def clear_todos():
    """Clear all todos and return the empty-state HTML string."""
    _todos.clear()
    return '<p style="color:#94a3b8;margin:0">No items yet.</p>'


# ── Page ───────────────────────────────────────────────────────────────────────

@page
def App():
    return """
    <div style="max-width:680px;margin:2rem auto;padding:0 1rem;font-family:system-ui,sans-serif;">

        <h1 style="font-size:1.75rem;font-weight:800;color:#0f172a;margin:0 0 .25rem;">
            @server_action Demo
        </h1>
        <p style="color:#64748b;margin:0 0 .5rem;">
            Async Python functions registered as
            <code style="background:#f1f5f9;padding:.1rem .35rem;border-radius:4px;font-size:.85rem;">POST /__pyrex/actions/&lt;name&gt;</code>
            endpoints. A named JS proxy is auto-generated per action.
        </p>
        <p style="color:#94a3b8;font-size:.85rem;margin:0 0 2rem;">
            Open DevTools Network tab to watch the JSON requests and responses.
        </p>

        <!-- ── Demo 1: stateless greeting, returns JSON dict ── -->
        <section style="margin-bottom:2rem;background:white;border-radius:12px;padding:1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .25rem;">
                1 — Returns JSON dict
            </h2>
            <p style="font-size:.8rem;color:#94a3b8;margin:0 0 1rem;font-family:monospace;">
                async def get_greeting(name: str, language: str) -> dict
            </p>

            <div style="display:flex;gap:.75rem;margin-bottom:.75rem;flex-wrap:wrap;">
                <input id="greet-name"
                    placeholder="Your name"
                    value="Alice"
                    style="flex:1;min-width:120px;padding:.5rem .75rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem;" />
                <select id="greet-lang"
                    style="padding:.5rem .75rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem;background:white;">
                    <option value="english">English</option>
                    <option value="spanish">Spanish</option>
                    <option value="french">French</option>
                    <option value="german">German</option>
                    <option value="japanese">Japanese</option>
                    <option value="arabic">Arabic</option>
                </select>
                <button
                    onclick="get_greeting(document.getElementById('greet-name').value, document.getElementById('greet-lang').value).then(function(d){ var r=document.getElementById('greeting-result'); r.style.display='block'; document.getElementById('greet-text').textContent=d.greeting; document.getElementById('greet-lang-tag').textContent=d.language; })"
                    style="padding:.5rem 1.25rem;background:#6366f1;color:white;border:none;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;">
                    Greet
                </button>
            </div>

            <div id="greeting-result" style="display:none;padding:1rem;background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0;">
                <p id="greet-text" style="margin:0;font-size:1.4rem;font-weight:700;color:#166534;"></p>
                <p style="margin:.25rem 0 0;font-size:.8rem;color:#4ade80;">
                    Computed on the Python server &mdash; language: <span id="greet-lang-tag"></span>
                </p>
            </div>
        </section>

        <!-- ── Demo 2: stateful todo list, returns HTML string ── -->
        <section style="background:white;border-radius:12px;padding:1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .25rem;">
                2 — Returns HTML string (assigned to innerHTML)
            </h2>
            <p style="font-size:.8rem;color:#94a3b8;margin:0 0 1rem;font-family:monospace;">
                async def add_todo(text: str) -> str
            </p>

            <div style="display:flex;gap:.75rem;margin-bottom:1rem;">
                <input id="todo-input"
                    placeholder="New todo..."
                    style="flex:1;padding:.5rem .75rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem;"
                    onkeydown="if(event.key==='Enter'){var inp=this;add_todo(inp.value).then(function(html){document.getElementById('todo-list').innerHTML=html;});inp.value='';}" />
                <button
                    onclick="var inp=document.getElementById('todo-input');add_todo(inp.value).then(function(html){document.getElementById('todo-list').innerHTML=html;});inp.value='';"
                    style="padding:.5rem 1.25rem;background:#6366f1;color:white;border:none;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;">
                    Add
                </button>
                <button
                    onclick="clear_todos().then(function(html){document.getElementById('todo-list').innerHTML=html;})"
                    style="padding:.5rem 1rem;background:#f1f5f9;color:#64748b;border:none;border-radius:6px;font-size:.9rem;cursor:pointer;">
                    Clear
                </button>
            </div>

            <div id="todo-list">
                <p style="color:#94a3b8;margin:0">No items yet.</p>
            </div>
        </section>

    </div>
    """
