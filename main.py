from pyrex import Pyrex

app = Pyrex()
app.config(styling="tailwind")


# @app.on_startup
# async def connect():
#     """Run once when the server starts (e.g. open DB connection)."""
#     pass

# @app.on_shutdown
# async def disconnect():
#     """Run once when the server stops (e.g. close DB connection)."""
#     pass

if __name__ == "__main__":
    app.run()  # PORT, PYREX_MODE, PYREX_SECRET_KEY are read from .env
