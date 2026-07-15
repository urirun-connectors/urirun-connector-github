# urirun-connector-github

GitHub / git connector for [ifURI](https://ifuri.com) / urirun. **Bootstrap a
project onto any computer over the URI contract:** clone a repo, pull updates,
pip install it, and emit its `urirun_bindings()` so the project can be compiled
into a registry and **served or `host deploy`-ed without ever logging into the
machine**.

| URI | Operation |
| --- | --- |
| `github://host/repo/command/clone` | `git clone` a repository |
| `github://host/repo/command/pull` | `git pull --ff-only` an existing clone |
| `github://host/repo/query/list` | list cloned repos under the projects root |
| `github://host/package/command/install` | `pip install -e` a cloned package |
| `github://host/repo/query/bindings` | emit a repo's urirun bindings (deployable) |
| `github://host/auth/query/status` | check `gh` authentication without exposing a token |
| `github://host/auth/command/import-to-vault` | validate the active `gh` token and store it in vault |
| `github://host/repo/command/create` | create a repository through `gh repo create` |

## GitHub CLI token and vault

The token is read only from `gh auth token`; it is never accepted in the URI
payload and never returned in a result. The import process validates it against
GitHub `/user`, then stores it as `api_key` in the configured vault:

```bash
export URIRUN_VAULT_URL=http://127.0.0.1:8130
export URIRUN_VAULT_TOKEN=... # service credential, not a GitHub token
urirun run 'github://host/auth/command/import-to-vault' \
  --payload '{"vault_entry_id":"github-cli-runtime"}' --execute
```

If `gh auth status` reports an invalid token, the import is refused. Re-run
`gh auth login -h github.com` before retrying.

Clones land under `URIRUN_PROJECTS` (default `~/.urirun-projects`).

## Why

It closes the loop for the mesh: a node can pull a new connector/pack/project
from git and start serving it, driven entirely by URIs — no SSH, no manual pip.

```
github://…/repo/command/clone  {url}      ── git clone onto the node
github://…/repo/query/bindings {dest,module} ── get its urirun_bindings()
        ▼
urirun compile  →  registry  →  serve  /  urirun host deploy   (on this box or any other)
```

## Use

```bash
# clone a connector repo and read its bindings out, ready to deploy
urirun run 'github://host/repo/command/clone' \
  --payload '{"url":"https://github.com/if-uri/urirun-connector-time-tools.git"}' --execute
urirun run 'github://host/repo/query/bindings' \
  --payload '{"dest":"~/.urirun-projects/urirun-connector-time-tools","module":"urirun_connector_time_tools"}' --execute
# -> {"bindings": {"time://host/clock/query/now": {...}}}  → compile + serve / host deploy
```

Live round-trip (clone a repo, extract its bindings):

```
github://…/repo/command/clone   -> {"ok":true,"repo":".../time-tools"}
github://…/repo/query/bindings  -> {"ok":true,"bindings":{"time://host/clock/query/now":{...}}}
```

## `repo_bindings` — the deploy bootstrap

`github://host/repo/query/bindings` emits a cloned repo's bindings document so it
can be compiled and served/deployed anywhere:

- pass `module` (the python package, e.g. `urirun_connector_time_tools`) — the
  repo dir is put on `PYTHONPATH` and `urirun_bindings()` is called out-of-process;
- or, with no `module`, the connector returns the first `*.bindings.json` it finds
  in the repo.

## Files

```
urirun_connector_github/core.py   # the github:// routes
connector.manifest.json           # prose manifest
tests/test_github.py              # offline (git/subprocess mocked)
```

## Test

```bash
pip install -e . && python3 -m pytest -q
```
