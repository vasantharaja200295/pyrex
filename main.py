from pyrex import Pyrex

app = Pyrex()

# @app.on_startup
# async def connect():
#     """Run once when the server starts (e.g. open DB connection)."""
#     pass

# @app.on_shutdown
# async def disconnect():
#     """Run once when the server stops (e.g. close DB connection)."""
#     pass

if __name__ == "__main__":
    app.run(directory="app", port=3000, debug=False, secret_key="3242kffjskldfjfhsdj3242342fd")
