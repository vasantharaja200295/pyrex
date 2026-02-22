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

@page
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
    is_demo   = True
    demo_note = (
        '<div style="background:#fef9c3;color:#854d0e;padding:.6rem 1rem;border-radius:6px;font-size:.875rem;margin-bottom:1.5rem;">'
        'Demo mode — all content below is static HTML baked at transpile time.'
        '</div>'
        if is_demo else ""
    )

    return """
    <div style="max-width:740px;margin:2rem auto;padding:0 1rem;">

        <h1 style="font-size:1.75rem;font-weight:800;color:#0f172a;margin:0 0 .25rem;">{page_title}</h1>
        <p style="color:#64748b;margin:0 0 .25rem;">{tagline}</p>
        <p style="font-size:.7rem;letter-spacing:.1em;font-weight:700;color:#94a3b8;margin:0 0 1.75rem;">{subtitle}</p>

        {demo_note}

        <!-- ── Section 1: local vars + method calls ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">1 — Local vars + method calls</h2>
            <div style="background:white;border-radius:8px;padding:1rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
                <p style="margin:0;font-size:.9rem;color:#334155;">
                    <code>page_title.upper()</code> → <strong>{subtitle}</strong>
                </p>
            </div>
        </section>

        <!-- ── Section 2: arithmetic ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">2 — Arithmetic at transpile time</h2>
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:1rem 1.5rem;">
                <code style="font-size:.9rem;color:#14532d;">a = {a}, b = {b}, a * b = {product}</code>
            </div>
        </section>

        <!-- ── Section 3: f-strings ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">3 — f-string interpolation</h2>
            <div style="background:white;border-radius:8px;padding:1rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
                <p style="margin:0;color:#334155;">{tagline}</p>
            </div>
        </section>

        <!-- ── Section 4: inner function + inline call ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">4 — Inner function — inline call in JSX</h2>
            <div style="background:white;border-radius:8px;padding:1rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
                <p style="margin:0 0 .5rem;font-size:.85rem;color:#64748b;">Calling <code>pill()</code> directly inside JSX curly braces:</p>
                <div>{pill("Python", "#fef3c7", "#92400e")} {pill("HTML", "#fce7f3", "#9d174d")} {pill("CSS", "#ede9fe", "#6d28d9")}</div>
            </div>
        </section>

        <!-- ── Section 5: list rendering via map ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">5 — List rendering via map + join</h2>
            <div style="background:white;border-radius:8px;padding:1rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
                <p style="margin:0 0 .5rem;font-size:.85rem;color:#64748b;"><code>pills_html = "".join(pill(t, ...) for t in tags)</code></p>
                <div>{pills_html}</div>
            </div>
        </section>

        <!-- ── Section 6: conditional rendering ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">6 — Conditional rendering</h2>
            <div style="background:white;border-radius:8px;padding:1rem 1.5rem;box-shadow:0 1px 4px rgba(0,0,0,.08);">
                <p style="margin:0 0 .5rem;font-size:.85rem;color:#64748b;">Inline ternary text: is_demo = {is_demo} →</p>
                <strong>{"Demo mode active" if is_demo else "Production mode"}</strong>
            </div>
        </section>

        <!-- ── Section 7: sub-component with prop expressions ── -->
        <section style="margin-bottom:2rem;">
            <h2 style="font-size:1rem;font-weight:700;color:#475569;margin:0 0 .5rem;">7 — Sub-component (StatCard) using props + f-string + .upper()</h2>
            <div style="display:flex;gap:1rem;flex-wrap:wrap;">
                <StatCard label="requests" value="1024" unit="/ day"/>
                <StatCard label="latency"  value="12"   unit="ms"/>
                <StatCard label="uptime"   value="99.9" unit="%"/>
            </div>
        </section>

        <!-- ── Section 8: use_state reactive counter alongside all the above ── -->
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
