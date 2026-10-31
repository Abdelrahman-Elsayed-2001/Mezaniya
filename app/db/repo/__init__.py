"""Repository layer: every function takes the current user's id first and never
touches another user's rows."""
from .base import Conflict, NotFound

__all__ = ["Conflict", "NotFound"]
