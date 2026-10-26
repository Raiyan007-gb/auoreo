"""H1b secrets (spec §25.14): rotate with a grace window, names-only list, age export/import
with an audit receipt, and the keyring tier forced on exposed binds."""

from __future__ import annotations

import builtins
import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.cli.serve_extras import ServeOptionError, resolve
from auoreo.core import keyring_policy, secrets_backup
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings

runner = CliRunner()
VALUE = "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123"


@pytest.fixture
def cli_env(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AUOREO_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_rotate_keeps_prev_inside_the_grace_window(home: Path) -> None:
    store = SecretStore(home, "allow-file")
    store.set("svc/key", "old-value-1234567")
    assert store.rotate("svc/key", "new-value-7654321", grace_s=3600, now=1_000)
    assert store.get("svc/key") == "new-value-7654321"
    # 1_000 + 3600 is long past → the resolver no longer serves #prev
    assert store.get("svc/key#prev") is None and store.candidates("svc/key") == ["new-value-7654321"]
    assert store.purge_expired() == ["svc/key"] and "svc/key#prev" not in store.names()
    store.rotate("svc/key", "third-value-000000", grace_s=3600)
    assert store.get("svc/key#prev") == "new-value-7654321"
    assert store.candidates("svc/key") == ["third-value-000000", "new-value-7654321"]
    assert not store.rotate("svc/key", "fourth-value-00000", grace_s=0)  # no grace: old one dropped
    assert store.get("svc/key#prev") is None and "svc/key#rotated_at" not in store.names()


def test_cli_rotate_grace_and_list_modes(cli_env: Path, home: Path) -> None:
    store = SecretStore(home, "allow-file")
    store.set("svc/token", VALUE)
    r = runner.invoke(app, ["secrets", "rotate", "svc/token", "--grace", "2h", "--generate"])
    assert r.exit_code == 0 and "#prev until" in r.output and VALUE not in r.output
    listed = runner.invoke(app, ["secrets", "list"])
    assert listed.exit_code == 0
    assert set(listed.output.split()) == {"svc/token", "svc/token#prev"}  # names only, no metadata
    masked = runner.invoke(app, ["secrets", "list", "--masked"])
    assert "sk-o…0123" in masked.output and VALUE not in masked.output
    assert runner.invoke(app, ["secrets", "rotate", "svc/token", "--grace", "soon"]).exit_code != 0


def test_age_export_import_roundtrip_with_audit_receipt(cli_env: Path, home: Path, tmp_path: Path) -> None:
    pyrage = pytest.importorskip("pyrage")
    a, b = pyrage.x25519.Identity.generate(), pyrage.x25519.Identity.generate()
    store = SecretStore(home, "allow-file")
    store.set("svc/token", VALUE)
    out = tmp_path / "b.age"
    args = ["secrets", "export", "--to", str(out), "-r", str(a.to_public()), "-r", str(b.to_public())]
    r = runner.invoke(app, args)
    assert r.exit_code == 0, r.output
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    blob = out.read_bytes()
    assert VALUE.encode() not in blob and blob.startswith(b"age-encryption.org/v1")
    key_file = (home / ".secrets.key").read_bytes().strip()
    plain = pyrage.decrypt(blob, [b])  # either recipient can open it
    assert key_file not in plain and b"secrets-key" not in plain  # never the Fernet key
    doc = json.loads(plain)
    assert doc["schema_version"] == 1 and {"name": "svc/token", "value": VALUE} in doc["entries"]
    receipt = secrets_backup.recipients_hash([str(a.to_public()), str(b.to_public())])
    assert receipt in r.output
    rows = _audit_rows(home, "secrets_export")
    assert rows and rows[-1]["recipients_hash"] == receipt and VALUE not in json.dumps(rows)

    other_home = tmp_path / "other"
    ident = tmp_path / "id.txt"
    ident.write_text(str(a) + "\n")
    r = runner.invoke(
        app, ["secrets", "import", str(out), "--identity", str(ident)], env={"AUOREO_HOME": str(other_home)}
    )
    assert r.exit_code == 0, r.output
    assert SecretStore(other_home, "allow-file").get("svc/token") == VALUE
    wrong = tmp_path / "wrong.txt"
    wrong.write_text(str(pyrage.x25519.Identity.generate()) + "\n")
    assert runner.invoke(app, ["secrets", "import", str(out), "-i", str(wrong)]).exit_code == 1


def _audit_rows(home: Path, action: str) -> list[dict]:  # type: ignore[type-arg]
    import sqlite3

    con = sqlite3.connect(home / "state.db")
    try:
        rows = con.execute("SELECT detail FROM audit WHERE action = ?", (action,)).fetchall()
    finally:
        con.close()
    return [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in rows]


def test_export_refuses_without_recipients_and_is_honest_without_pyrage(
    cli_env: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SecretStore(home, "allow-file").set("svc/token", VALUE)
    r = runner.invoke(app, ["secrets", "export"])
    assert r.exit_code == 2 and "--recipient" in r.output
    real = builtins.__import__

    def no_pyrage(name: str, *a: object, **kw: object) -> object:
        if name == "pyrage":
            raise ImportError("no pyrage")
        return real(name, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_pyrage)
    r = runner.invoke(app, ["secrets", "export", "-r", "age1qqqq"])
    assert r.exit_code == 1 and "pyrage" in r.output and "uv sync --extra age" in r.output
    assert VALUE not in r.output


# ------------------------------------------------------------------ keyring tiers
class _PlaintextFile:
    priority = 0.5


_PlaintextFile.__module__ = "keyrings.alt.file"
_PlaintextFile.__name__ = "PlaintextKeyring"


def test_exposed_bind_forces_forbid_file(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUOREO_KEYRING", raising=False)
    st = Settings(home=home, host="0.0.0.0", server_password="pw")
    resolve(st)
    assert st.keyring == "forbid-file"
    local = Settings(home=home)
    resolve(local)
    assert local.keyring == "allow-file"
    explicit = Settings(home=home, host="0.0.0.0", server_password="pw", keyring="allow-file")
    resolve(explicit)
    assert explicit.keyring == "allow-file"  # an explicit choice is an override


def test_exposed_bind_refuses_plaintext_file_keyring(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring_policy, "_backend", lambda: _PlaintextFile())
    st = Settings(home=home, host="0.0.0.0", server_password="pw")
    with pytest.raises(ServeOptionError, match="plaintext file"):
        resolve(st)
    assert keyring_policy.doctor_row(st)[1] == "error"
    assert not keyring_policy.backend_allowed("forbid-file") and keyring_policy.backend_allowed("allow-file")


def test_forbid_file_store_ignores_the_key_file(home: Path) -> None:
    SecretStore(home, "allow-file").set("a", "b")  # no OS keyring here → 0600 key file
    from auoreo.core.secrets import SecretStoreError

    with pytest.raises(SecretStoreError):
        SecretStore(home, "forbid-file").names()


def test_doctor_keyring_row_shape(home: Path) -> None:
    check, level, detail, _ = keyring_policy.doctor_row(Settings(home=home))
    assert check == "keyring" and level in {"ok", "warn"}
    assert "viable=" in detail and "headless=" in detail and "policy=" in detail
