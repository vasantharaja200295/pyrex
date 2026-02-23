from pyrex import page, server_action

# ── Server actions run on the Python server, never in the browser ─────────────
#
# Call from JSX event handlers:
#   pyrex_action('action_name', {key: value}, '#target-selector')
#
# The return value (HTML string) replaces the target element's innerHTML.


# ── 1. Stateless action: pure computation, no side effects ────────────────────

@server_action
def get_greeting(name: str, language: str):
    """Return a greeting in the requested language — computed server-side."""
    phrases = {
        "english":  "Hello",
        "spanish":  "Hola",
        "french":   "Bonjour",
        "german":   "Hallo",
        "japanese": "Konnichiwa",
        "arabic":   "Marhaba",
    }
    greeting = phrases.get(language.lower(), "Hello")
    lang_label = language.capitalize()
    return (
        f'<div style="padding:1rem;background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0;">'
        f'<p style="margin:0;font-size:1.5rem;font-weight:700;color:#166534;">'
        f'{greeting}, {name}!</p>'
        f'<p style="margin:.25rem 0 0;font-size:.8rem;color:#4ade80;">'
        f'Rendered in Python on the server &mdash; language: {lang_label}</p>'
        f'</div>'
    )


# ── 2. Stateful action: in-memory list persists across requests ───────────────
#    (state resets on server restart or when this file is saved)

_todos: list[str] = []

@server_action
def add_todo(text: str):
    """Append a todo item and return the full updated list HTML."""
    text = text.strip()
    if text:
        _todos.append(text)
    if not _todos:
        return '<p style="color:#94a3b8;margin:0;">No items yet.</p>'
    items = "".join(
        f'<li style="padding:.4rem 0;border-bottom:1px solid #f1f5f9;color:#334155;">'
        f'{i + 1}. {item}'
        f'</li>'
        for i, item in enumerate(_todos)
    )
    return f'<ul style="margin:0;padding:0;list-style:none;">{items}</ul>'


@server_action
def clear_todos():
    """Clear all todos and return empty state HTML."""
    _todos.clear()
    return '<p style="color:#94a3b8;margin:0;">No items yet.</p>'


# ── Page ──────────────────────────────────────────────────────────────────────

@page
def App():
    return """
    <div style="max-width:680px;margin:2rem auto;padding:0 1rem;font-family:system-ui,sans-serif;">

        <h1 style="font-size:1.75rem;font-weight:800;color:#0f172a;margin:0 0 .25rem;">
            @server_action Demo
        </h1>
        <p style="color:#64748b;margin:0 0 2rem;">
            Python functions that run on the server when called from the browser.
            No API routes. No fetch boilerplate. Just decorated Python.
        </p>

        <!-- ── Demo 1: stateless greeting ── -->
        <section style="margin-bottom:2rem;background:white;border-radius:12px;padding:1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 1rem;">
                1 — Stateless action (pure computation)
            </h2>

            <div style="display:flex;gap:.75rem;margin-bottom:.75rem;flex-wrap:wrap;">
                <input id="greet-name"
                    placeholder="Your name"
                    style="flex:1;min-width:120px;padding:.5rem .75rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem;"
                    value="Alice" />
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
                    onclick="pyrex_action('get_greeting', {name: document.getElementById('greet-name').value, language: document.getElementById('greet-lang').value}, '#greeting-result')"
                    style="padding:.5rem 1.25rem;background:#6366f1;color:white;border:none;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;">
                    Greet
                </button>
            </div>

            <div id="greeting-result" style="min-height:3rem;">
                <p style="color:#94a3b8;margin:0;font-size:.9rem;">
                    Press Greet to call the server action.
                </p>
            </div>
        </section>

        <!-- ── Demo 2: stateful todo list ── -->
        <section style="background:white;border-radius:12px;padding:1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 1rem;">
                2 — Stateful action (in-memory list on server)
            </h2>

            <div style="display:flex;gap:.75rem;margin-bottom:1rem;">
                <input id="todo-input"
                    placeholder="New todo..."
                    style="flex:1;padding:.5rem .75rem;border:1px solid #e2e8f0;border-radius:6px;font-size:.9rem;"
                    onkeydown="if(event.key==='Enter') pyrex_action('add_todo', {text: this.value}, '#todo-list'); this.value='';" />
                <button
                    onclick="pyrex_action('add_todo', {text: document.getElementById('todo-input').value}, '#todo-list'); document.getElementById('todo-input').value='';"
                    style="padding:.5rem 1.25rem;background:#6366f1;color:white;border:none;border-radius:6px;font-size:.9rem;font-weight:600;cursor:pointer;">
                    Add
                </button>
                <button
                    onclick="pyrex_action('clear_todos', {}, '#todo-list')"
                    style="padding:.5rem 1rem;background:#f1f5f9;color:#64748b;border:none;border-radius:6px;font-size:.9rem;cursor:pointer;">
                    Clear
                </button>
            </div>

            <div id="todo-list">
                <p style="color:#94a3b8;margin:0;">No items yet.</p>
            </div>
        </section>

    </div>
    """
