from pyrex import page, component, use_state


@component
def Header():
    return """
    <header style="background:#0f172a;color:white;padding:1rem 2rem;display:flex;align-items:center;gap:1rem;">
        <span style="font-size:1.5rem;font-weight:700;">🔥 Pyrex</span>
        <span style="color:#94a3b8;font-size:0.9rem;">Python → HTML Framework POC</span>
    </header>
    """

@page
def App():
    return """
    <div style="min-height:100vh;background:#f8fafc;">
        <Header />
        <div>
            <h1>Hello im working as expected</h1>
        </div>
    </div>
    """
