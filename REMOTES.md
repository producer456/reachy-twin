# Repo remotes & backup notes

This repo (`reachy-twin`) lives in more than one place. Read this before assuming
where the "real" copy is.

## Remotes (as of 2026-06-19)

| Remote | URL | Role |
|---|---|---|
| `origin` | `producer456/reachy-twin` | **Canonical.** Has the full history. Push here. |
| `upstream` | `producer456hub/reachy-twin` | Intended shared upstream — **NOT pushable from the `producer456` account.** |
| `mirror` | `producer456/reachy-twin-mirror` | **Redundant backup** of `origin`, under the `producer456` account. |

## ⚠️ Heads-up (the thing to remember)

- **`mirror` is a complete duplicate of `origin`, not new content.** It was created
  on 2026-06-19 as an extra backup under the accessible `producer456` account.
  Everything in `mirror` is already in `origin`.
- **The `producer456hub` upstream is a separate problem.** Pushing there fails with
  `403 — Permission to producer456hub/reachy-twin.git denied to producer456`. The
  `mirror` does **not** fix that. To get work onto `upstream` you need either:
  - to authenticate as the `producer456hub` account (`gh auth switch` / `gh auth login`), or
  - to add `producer456` as a write collaborator on `producer456hub/reachy-twin`.
- So: backups are covered (origin + mirror), but the shared `producer456hub` copy
  stays stale until the credentials/permissions above are sorted.
