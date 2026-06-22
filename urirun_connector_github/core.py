# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""GitHub / git connector for urirun — clone a repo, then deploy it anywhere.

The point: bootstrap a new project (a connector, a pack, a whole registry) onto
ANY computer over the URI contract. Clone the repo, pull updates, optionally pip
install it, and — crucially — emit its `urirun_bindings()` so the result can be
compiled into a registry and served / `host deploy`-ed without ever logging into
the machine.

Routes:

* ``github://host/repo/command/clone``    -- git clone a repo
* ``github://host/repo/command/pull``     -- git pull (ff-only) an existing clone
* ``github://host/repo/query/list``       -- list cloned repos under the projects root
* ``github://host/package/command/install`` -- pip install (-e) a cloned package
* ``github://host/repo/query/bindings``   -- emit a repo's urirun bindings (deployable)

Clones land under ``URIRUN_PROJECTS`` (default ``~/.urirun-projects``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import urirun

CONNECTOR_ID = "github"
conn = urirun.connector(CONNECTOR_ID, scheme="github")


def _root() -> Path:
    return Path(os.environ.get("URIRUN_PROJECTS", str(Path.home() / ".urirun-projects")))


def _name(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _git(args: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)


@conn.handler("repo/command/clone", isolated=True, meta={"label": "git clone a repository"})
def clone(url: str = "", dest: str = "", branch: str = "", depth: int = 0) -> dict[str, Any]:
    """Clone `url` (into `dest`, or ~/.urirun-projects/<name>). Idempotent: an
    existing clone is reported, not re-cloned."""
    if not url:
        return urirun.fail("url is required")
    target = Path(dest) if dest else _root() / _name(url)
    if (target / ".git").exists():
        return urirun.ok(repo=str(target), url=url, existed=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"]
    if branch:
        args += ["--branch", branch]
    if depth:
        args += ["--depth", str(int(depth))]
    args += [url, str(target)]
    proc = _git(args)
    if proc.returncode != 0:
        return urirun.fail((proc.stderr or proc.stdout).strip()[-400:], url=url)
    return urirun.ok(repo=str(target), url=url, existed=False)


@conn.handler("repo/command/pull", isolated=True, meta={"label": "git pull an existing clone"})
def pull(dest: str = "") -> dict[str, Any]:
    if not dest or not (Path(dest) / ".git").exists():
        return urirun.fail(f"not a git checkout: {dest}")
    proc = _git(["-C", dest, "pull", "--ff-only"], timeout=120)
    if proc.returncode != 0:
        return urirun.fail(proc.stderr.strip()[-300:], repo=dest)
    return urirun.ok(repo=dest, output=(proc.stdout or proc.stderr).strip()[-200:])


@conn.handler("repo/query/list", isolated=True, meta={"label": "List cloned repositories"})
def list_repos(root: str = "") -> dict[str, Any]:
    base = Path(root) if root else _root()
    repos = sorted(str(d) for d in base.glob("*") if (d / ".git").exists()) if base.exists() else []
    return urirun.ok(root=str(base), repos=repos, count=len(repos))


@conn.handler("package/command/install", isolated=True, meta={"label": "pip install a cloned package"})
def install(dest: str = "", editable: bool = True, no_deps: bool = True) -> dict[str, Any]:
    if not Path(dest).exists():
        return urirun.fail(f"no such directory: {dest}")
    cmd = [sys.executable, "-m", "pip", "install", "--no-input"]
    if no_deps:
        cmd.append("--no-deps")
    if editable:
        cmd.append("-e")
    cmd.append(dest)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        return urirun.fail((proc.stderr or proc.stdout).strip()[-400:], dest=dest)
    return urirun.ok(installed=dest, editable=bool(editable))


@conn.handler("repo/query/bindings", isolated=True, meta={"label": "Emit a repo's urirun bindings"})
def repo_bindings(dest: str = "", module: str = "") -> dict[str, Any]:
    """Emit the repo's `urirun_bindings()` as a deployable document. Pass `module`
    (the python package, e.g. `urirun_connector_time_tools`); the repo dir is added
    to the path. Falls back to any `*.bindings.json` in the repo when no module is
    given. The returned `bindings` can be compiled into a registry and served or
    pushed with `host deploy` — onto this machine or any other."""
    repo = Path(dest)
    if not repo.exists():
        return urirun.fail(f"no such directory: {dest}")
    if module:
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(repo), os.environ.get("PYTHONPATH", "")]))
        code = f"import json,{module} as m; print(json.dumps(m.urirun_bindings()))"
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
        if proc.returncode != 0:
            return urirun.fail((proc.stderr or proc.stdout).strip()[-300:], module=module)
        return urirun.ok(module=module, **json.loads(proc.stdout))
    found = sorted(repo.glob("*.bindings.json")) or sorted(repo.glob("**/*.bindings.json"))
    if found:
        return urirun.ok(source=str(found[0]), **json.loads(found[0].read_text(encoding="utf-8")))
    return urirun.fail("no `module` given and no *.bindings.json found in the repo")


# --- authoring surface -----------------------------------------------------

def urirun_bindings() -> dict[str, Any]:
    return conn.bindings()


def connector_manifest() -> dict[str, Any]:
    return conn.manifest(urirun.load_manifest(__package__))


def main(argv: list[str] | None = None) -> int:
    return conn.cli(argv, manifest_prose=urirun.load_manifest(__package__))


if __name__ == "__main__":
    raise SystemExit(main())
