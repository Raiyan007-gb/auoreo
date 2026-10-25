"""`policies/<name>.star` folder kind (M6 shared contract)."""

from __future__ import annotations

from pathlib import Path

from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness


def test_policy_header_and_decide_required(tmp_path: Path) -> None:
    pol = tmp_path / "policies"
    pol.mkdir()
    (pol / "deny_curl.star").write_text(
        "# description: deny curl piped to sh\n# priority: 500\n\n"
        'def decide(req):\n    return "deny" if "| sh" in req["pattern"] else None\n'
    )
    (pol / "broken.star").write_text("# description: no decide\nx = 1\n")
    h = Harness([Root("runtime", tmp_path)])
    assert "deny_curl" in h.policies
    m = h.policies["deny_curl"].manifest
    assert (m.priority, m.enabled) == (500, True)
    assert any("decide" in str(e) for e in h.errors)
    rows = h.describe()["policies"]
    assert rows[0]["name"] == "deny_curl"
