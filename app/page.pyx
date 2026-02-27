from pyrex import page, component, use_state



# ── Helper component: uses props + f-string + .upper() ───────────────────────

@component
def StatCard(label, value, unit):
    display = f"{value} {unit}"
    loud    = label.upper()
    return """
    <div style="background:white;border-radius:8px;padding:1.25rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);min-width:140px;">
        <div style="font-size:.75rem;font-weight:600;color:#64748b;letter-spacing:.05em;">{loud}</div>
        <div style="font-size:1.75rem;font-weight:700;color:#0f172a;margin-top:.25rem;">{display}</div>
    </div>
    """


# ── Root page: demonstrates all expression features ───────────────────────────

@page(title="Home — My App",
    favicon="/static/favicon.ico",
    meta={
        "description": "Welcome to my Pyrex app",
        "og:title": "Home — My App",
        "og:image": "/static/og.png",

    },)
def App():
    count, set_count = use_state(0)

    # ── 1. Simple local vars + method calls ──────────────────────────────────
    page_title = "Pyrex Expression Features"
    subtitle   = page_title.upper()           # method call on local var

    # ── 2. Arithmetic ─────────────────────────────────────────────────────────
    a       = 6
    b       = 7
    product = a * b

    # ── 3. f-string ───────────────────────────────────────────────────────────
    framework = "Pyrex"
    tagline   = f"Built with {framework} — zero JS, pure Python"

    # ── 4. Inner function def (helper called in local var + inline in JSX) ────
    def pill(text, bg, fg):
        return f'<span style="background:{bg};color:{fg};padding:.2rem .7rem;border-radius:999px;font-size:.8rem;font-weight:600;margin-right:.4rem;">{text}</span>'

    # ── 5. List + map via inner function ──────────────────────────────────────
    tags      = ["server-rendered", "no-npm", "no-bundler", "vanilla-js"]
    pills_html = "".join(pill(t, "#e0e7ff", "#4338ca") for t in tags)

    # ── 6. Conditional rendering (Python ternary → HTML block) ───────────────
    is_demo   = False
    demo_note = (
        '<div style="background:#fef9c3;color:#854d0e;padding:.6rem 1rem;border-radius:6px;font-size:.875rem;margin-bottom:1.5rem;">'
        'Demo mode — all content below is static HTML baked at transpile time.'
        '</div>'
        if is_demo else ""
    )
    # Conditional that returns an HTML element — must be a Python string, not raw markup.
    # Wrong:  {<p style="...">text</p> if flag else ""}   ← not valid Python
    # Right:  assign to local var, use single quotes inside to avoid quote conflicts
    mode_badge = (
        '<p style="font-size:1rem;color:#2563eb;font-weight:600;margin:0;">Demo mode active</p>'
        if is_demo else
        '<p style="font-size:1rem;color:#6b7280;margin:0;">Production mode</p>'
    )

    return """
    <div style="max-width:740px;margin:2rem auto;padding:0 1rem;">
        <section>
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">8 — use_state reactive counter (client JS)</h2>
            <div style="background:white;border-radius:8px;padding:1.25rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;align-items:center;gap:1rem;">
                <button onclick="set_count(count - 1)" style="width:36px;height:36px;border:none;border-radius:6px;background:#f1f5f9;font-size:1.25rem;cursor:pointer;">-</button>
                <span style="font-size:1.5rem;font-weight:700;color:#0f172a;min-width:2rem;text-align:center;">{count}</span>
                <button onclick="set_count(count + 1)" style="width:36px;height:36px;border:none;border-radius:6px;background:#f1f5f9;font-size:1.25rem;cursor:pointer;">+</button>
                <span style="color:#64748b;font-size:.85rem;">sections 1-7 are static HTML — only this counter uses JS</span>
            </div>
        </section>

    </div>
    """
