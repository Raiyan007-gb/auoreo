"""Teams (spec §16.1–16.6): delegation to child sessions, mailbox, kanban-lite.

Started through `core.extensions.STARTERS` as `auoreo.teams:start`, so the service is
available as `services.ext["teams"]` (the extension key is the module's last dotted part).
"""

from auoreo.teams.service import Member, TeamsService, start

__all__ = ["Member", "TeamsService", "start"]
