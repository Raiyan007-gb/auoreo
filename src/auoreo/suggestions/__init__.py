"""Proactive suggestions: started as `auoreo.suggestions:start` → `services.ext["suggestions"]`."""

from auoreo.suggestions.engine import (
    TOMBSTONES,
    Accepted,
    SuggestionEngine,
    SuggestionError,
    start,
    suggestions_of,
)

__all__ = ["TOMBSTONES", "Accepted", "SuggestionEngine", "SuggestionError", "start", "suggestions_of"]
