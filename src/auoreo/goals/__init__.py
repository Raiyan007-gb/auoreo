"""Goals (spec §2.3): started as `auoreo.goals:start` → `services.ext["goals"]`."""

from auoreo.goals.service import GoalError, GoalsService, goals_of, start

__all__ = ["GoalError", "GoalsService", "goals_of", "start"]
