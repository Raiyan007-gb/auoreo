"""Automation (M5a/M5b): webhook ingress, scheduler, and the deliveries outbox.

Only the process holding `<home>/automation.lock` drains the outbox and fires schedules
(single writer); any process may enqueue.
"""
