# Data at rest

AUOREO keeps three databases under `AUOREO_HOME` (`~/.auoreo/`):

| File | Holds | Protected by |
|---|---|---|
| `state.db` | sessions, events, audit, approvals | `db_encryption` (below) |
| `memory.db` | memory facts, chunks, transcript index (FTS5 + sqlite-vec) | `db_encryption = "sqlcipher"` only |
| `secrets.db` | provider keys and other secrets | always Fernet-encrypted values |

## `db_encryption`

| Mode | state.db | memory.db |
|---|---|---|
| `off` (default) | plaintext | plaintext |
| `columns` | `events.data` / `audit.detail` Fernet envelopes | **plaintext** |
| `sqlcipher` | whole file (SQLCipher 4, raw 256-bit key) | **whole file** (same key) |

`columns` stays a state.db-only mode: memory rows are searched by FTS5 and vector indexes that
cannot work over ciphertext columns. **To encrypt memory.db, use `sqlcipher`** (the `sqlcipher`
extra, `sqlcipher3`).

The key is `auoreo-db-key` in the OS keyring, else a 0600 `~/.auoreo/.db.key` (policy
`keyring`). One ring serves both files; opening tries every key in it, so an interrupted
rotation still opens.

### memory.db under SQLCipher (F6)

- The memory store opens `memory.db` through `sqlcipher3`, runs `PRAGMA key` first, then loads
  the sqlite-vec extension and creates its FTS5 tables exactly as on stdlib sqlite3. The
  `sqlcipher3` wheels ship FTS5 and allow loadable extensions (verified: SQLCipher 4.x community,
  sqlite-vec 0.1.x). If a build cannot load extensions, memory falls back to FTS-only search
  and `auoreo doctor` shows `memory_db_cipher` as a warning (`sqlite-vec on sqlcipher3:
  UNAVAILABLE`).
- A **plaintext memory.db is refused** in sqlcipher mode (the server does not start) with a
  pointer to `auoreo db encrypt`; an encrypted memory.db is refused in `off`/`columns` mode with
  a pointer to the setting. Nothing is ever silently re-created.

### Switching an existing install to SQLCipher

```bash
auoreo db backup ~/auoreo-backup --with-key ~/auoreo-key.txt   # keep the key elsewhere
# stop the server
auoreo db encrypt --yes          # state.db AND memory.db via sqlcipher_export; plaintext *.plain-<ts>.bak kept
auoreo config set db_encryption sqlcipher
auoreo doctor                    # db_encryption + memory_db_cipher rows
rm ~/.auoreo/*.plain-*.bak       # once verified
```

- `auoreo db rotate-key` adds a new primary key and `PRAGMA rekey`s every SQLCipher file
  (state.db and memory.db). Stop the server first.
- `auoreo db backup` uses the SQLite online-backup API; an encrypted file is copied into an
  encrypted file with the same key (`manifest.json` records `state_db_sqlcipher` /
  `memory_db_sqlcipher`). The key leaves only with `--with-key`.
- `auoreo db restore --yes [--key file]` copies the files back byte-for-byte (they stay
  encrypted) and merges the key file into the ring.
