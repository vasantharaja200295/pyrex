"""
lib/db.py — shared MongoDB connection for Pyrex app.

Import anywhere with:
    from lib.db import col
"""
import os
from pymongo import MongoClient

_MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
_client = MongoClient(_MONGO_URI)
_db = _client["pyrex_tasks"]


def col(name: str):
    """Return a collection from the pyrex_tasks database."""
    return _db[name]
