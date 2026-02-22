from pyrex import page, component, use_state


@component
def Header():
    return """
    <header style="background:#0f172a;color:white;padding:1rem 2rem;display:flex;align-items:center;gap:1rem;">
        <span style="font-size:1.5rem;font-weight:700;">🔥 Pyrex</span>
        <span style="color:#94a3b8;font-size:0.9rem;">Python → HTML Framework POC</span>
    </header>
    """


@component
def Counter():
    count, set_count = use_state(0)

    return """
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:2rem;max-width:320px;text-align:center;background:white;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
        <h2 style="margin:0 0 0.5rem;color:#1e293b;">Counter</h2>
        <p style="color:#64748b;margin:0 0 1.5rem;font-size:0.9rem;">Classic state demo</p>
        <div style="font-size:3rem;font-weight:700;color:#6366f1;margin-bottom:1.5rem;">{count}</div>
        <div style="display:flex;gap:0.75rem;justify-content:center;">
            <button onclick="set_count(count - 1)" style="padding:0.5rem 1.25rem;border-radius:8px;border:1px solid #e2e8f0;background:white;cursor:pointer;font-size:1rem;">-</button>
            <button onclick="set_count(0)" style="padding:0.5rem 1.25rem;border-radius:8px;border:1px solid #e2e8f0;background:white;cursor:pointer;font-size:0.9rem;color:#64748b;">Reset</button>
            <button onclick="set_count(count + 1)" style="padding:0.5rem 1.25rem;border-radius:8px;border:none;background:#6366f1;color:white;cursor:pointer;font-size:1rem;">+</button>
        </div>
    </div>
    """


@component
def GreetingForm():
    name, set_name = use_state("")

    return """
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:2rem;max-width:320px;background:white;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
        <h2 style="margin:0 0 0.5rem;color:#1e293b;">Greeting</h2>
        <p style="color:#64748b;margin:0 0 1.5rem;font-size:0.9rem;">Live input binding</p>
        <input
            data-bind="name"
            data-component="GreetingForm"
            oninput="set_name(this.value)"
            placeholder="Type your name..."
            style="width:100%;padding:0.625rem 0.75rem;border:1px solid #e2e8f0;border-radius:8px;font-size:1rem;outline:none;box-sizing:border-box;"
        />
        <div style="margin-top:1rem;padding:0.75rem 1rem;background:#f0fdf4;border-radius:8px;color:#16a34a;font-weight:500;min-height:2.5rem;">
            Hello, <span data-state="name" data-component="GreetingForm">...</span>!
        </div>
    </div>
    """


@component
def TodoList():
    remaining, set_remaining = use_state(3)

    return """
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:2rem;max-width:320px;background:white;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
        <h2 style="margin:0 0 0.5rem;color:#1e293b;">Tasks</h2>
        <p style="color:#64748b;margin:0 0 1rem;font-size:0.9rem;">
            <span data-state="remaining" data-component="TodoList">3</span> remaining
        </p>
        <div style="display:flex;flex-direction:column;gap:0.5rem;">
            <label style="display:flex;align-items:center;gap:0.75rem;padding:0.5rem;cursor:pointer;">
                <input type="checkbox" onchange="set_remaining(remaining - (this.checked ? 1 : -1))" />
                <span>Build the parser</span>
            </label>
            <label style="display:flex;align-items:center;gap:0.75rem;padding:0.5rem;cursor:pointer;">
                <input type="checkbox" onchange="set_remaining(remaining - (this.checked ? 1 : -1))" />
                <span>Write the transpiler</span>
            </label>
            <label style="display:flex;align-items:center;gap:0.75rem;padding:0.5rem;cursor:pointer;">
                <input type="checkbox" onchange="set_remaining(remaining - (this.checked ? 1 : -1))" />
                <span>Port to Go</span>
            </label>
        </div>
    </div>
    """


@page
def App():
    return """
    <div style="min-height:100vh;background:#f8fafc;">
        <Header />
        <main style="padding:3rem 2rem;max-width:1100px;margin:0 auto;">
            <div style="margin-bottom:2.5rem;">
                <h1 style="font-size:2rem;font-weight:700;color:#0f172a;margin:0 0 0.5rem;">
                    Pyrex POC
                </h1>
                <p style="color:#64748b;margin:0;font-size:1.1rem;">
                    Python .pyx files transpiled to HTML + vanilla JS. No React. No Node.
                </p>
            </div>
            <div style="display:flex;gap:1.5rem;flex-wrap:wrap;">
                <Counter />
                <GreetingForm />
                <TodoList />
            </div>
        </main>
    </div>
    """
