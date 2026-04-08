"""Shared slowapi Limiter instance.

Imported by app.main (which wires it into FastAPI app state) and by
individual routers that want to apply @limiter.limit decorators without
creating a circular import on app.main.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
