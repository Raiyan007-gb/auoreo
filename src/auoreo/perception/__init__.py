"""Perception helpers (W2-C): object/face detection and sensor probes.

The backends live in `backends/{detection,face,sensor}/<name>/`; this package holds the
shared, library-free pieces they and the tools use:

- `blobs`: a deterministic colour-blob detector (Pillow only) behind the `fake` backends.
- `draw`: annotate an image with boxes (Pillow).
- `models`: lazy-import / model-cache helpers and the licence rules for detection models.
- `sensors`: the session-scoped probe registry (started on first use, stopped when the
  session is archived or deleted) and RTSP/URL credential redaction.
- `rtc`: the optional in-memory RTC video frame store (`perception_sample_rtc_video`).

Privacy (spec L744): faces are off by default (`faces_enabled`), face embeddings are only
computed when asked for, returned to the caller and never persisted; frames are kept in
memory only.
"""
