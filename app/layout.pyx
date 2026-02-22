from pyrex import layout, component

@component
def Header():
    return """
    <header style="background:#0f172a;color:white;padding:1rem 2rem;display:flex;align-items:center;gap:1rem;">
        <span style="font-size:1.5rem;font-weight:700;">🔥 Pyrex</span>
        <span style="color:#94a3b8;font-size:0.9rem;">Python → HTML Framework POC</span>
        <a href='/' style="color:#94a3b8;font-size:0.9rem;">Home</a>
        <a href='/about' style="color:#94a3b8;font-size:0.9rem;">About</a>
        <a href='/profile/me' style="color:#94a3b8;font-size:0.9rem;">profile</a>
    </header>
    """

@layout
def Layout(children):
    return """ 
    <div style="min-height:100vh;background:#f8fafc;">
        <Header/>
        <div style="padding:20px; background:#f5ebe0; height:100vh; width:100vw;">{children}</div>
    </div>
    """