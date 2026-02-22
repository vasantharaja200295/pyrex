from pyrex import page, component, use_state


# ── Demo component: receives props, computes locals server-side ───────────────

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


# ── Demo component: arithmetic + method call ──────────────────────────────────

@component
def ExprDemo():
    a       = 6
    b       = 7
    product = a * b
    note    = "server-side"
    tag     = note.capitalize()
    return """
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:1rem 1.5rem;">
        <p style="margin:0 0 .5rem;font-weight:600;color:#166534;">Arithmetic + method calls</p>
        <code style="display:block;background:#dcfce7;border-radius:4px;padding:.5rem .75rem;font-size:.9rem;color:#14532d;">
            a = {a}, b = {b}, a * b = {product}
        </code>
        <p style="margin:.75rem 0 0;font-size:.85rem;color:#166534;">Evaluated {tag} at transpile time - zero JS needed.</p>
    </div>
    """


# ── Demo component: f-string + chained method ─────────────────────────────────

@component
def Greeting(name, role):
    msg      = f"Hello, {name}!"
    subtitle = f"{role} account"
    badge    = role.upper()
    return """
    <div style="display:flex;align-items:center;gap:1rem;background:white;border-radius:8px;padding:1rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
        <div style="width:48px;height:48px;border-radius:50%;background:#6366f1;display:flex;align-items:center;justify-content:center;color:white;font-weight:700;font-size:1.5rem;">
            {name}
        </div>
        <div>
            <div style="font-weight:700;color:#0f172a;">{msg}</div>
            <div style="font-size:.8rem;color:#64748b;">{subtitle}</div>
        </div>
        <span style="margin-left:auto;background:#e0e7ff;color:#4338ca;font-size:.7rem;font-weight:700;padding:.2rem .6rem;border-radius:999px;">{badge}</span>
    </div>
    """


# ── Root page ──────────────────────────────────────────────────────────────────

@page
def App():
    count, set_count = use_state(0)

    page_title  = "JSX Expressions Demo"
    description = "Local Python vars, f-strings, arithmetic and method calls - all evaluated at transpile time"
    subtitle    = page_title.upper()

    return """
    <div style="max-width:720px;margin:2rem auto;padding:0 1rem;">

        <h1 style="font-size:1.75rem;font-weight:800;color:#0f172a;margin:0 0 .25rem;">{page_title}</h1>
        <p style="color:#64748b;margin:0 0 .5rem;font-size:.95rem;">{description}</p>
        <div style="font-size:.7rem;letter-spacing:.1em;font-weight:700;color:#94a3b8;margin-bottom:2rem;">{subtitle}</div>

        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .75rem;">Props + local vars inside a component</h2>
            <Greeting name="Alice" role="admin"/>
        </section>

        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .75rem;">Stat cards (prop feeds f-string)</h2>
            <div style="display:flex;gap:1rem;flex-wrap:wrap;">
                <StatCard label="requests" value="1024" unit="/ day"/>
                <StatCard label="latency"  value="12"   unit="ms"/>
                <StatCard label="uptime"   value="99.9" unit="%"/>
            </div>
        </section>

        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .75rem;">Arithmetic + chained method calls</h2>
            <ExprDemo/>
        </section>

        <section>
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .75rem;">Reactive state (use_state) still works alongside static expressions</h2>
            <div style="background:white;border-radius:8px;padding:1.25rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;align-items:center;gap:1rem;">
                <button onclick="set_count(count - 1)" style="width:36px;height:36px;border:none;border-radius:6px;background:#f1f5f9;font-size:1.25rem;cursor:pointer;">-</button>
                <span style="font-size:1.5rem;font-weight:700;color:#0f172a;min-width:2rem;text-align:center;">{count}</span>
                <button onclick="set_count(count + 1)" style="width:36px;height:36px;border:none;border-radius:6px;background:#f1f5f9;font-size:1.25rem;cursor:pointer;">+</button>
                <span style="color:#64748b;font-size:.85rem;">clicks are handled client-side, everything else above is static HTML</span>
            </div>
        </section>

    </div>
    """
