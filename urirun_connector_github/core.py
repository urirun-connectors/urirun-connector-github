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
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import urirun

from . import _urirun_compat

CONNECTOR_ID = "github"
conn = _urirun_compat.connector(CONNECTOR_ID, scheme="github")
_SAFE_SLUG = __import__("re").compile(r"^[A-Za-z0-9_.-]{1,100}$")


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    token = _lease_github_token()
    if token:
        return token
    proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError("github_auth_unavailable")
    return proc.stdout.strip()


def _api(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    if not path.startswith("/") or ".." in path or "?" in path:
        raise RuntimeError("github_api_path_invalid")
    token = _token()
    request = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"accept":"application/vnd.github+json","authorization":f"Bearer {token}","x-github-api-version":"2022-11-28","content-type":"application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw=response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw=error.read().decode("utf-8",errors="replace")
        try: data=json.loads(raw) if raw else {}
        except json.JSONDecodeError: data={"message":"invalid_response"}
        return error.code,data
    finally:
        token=""


def _repo_path(owner: str, repo: str, suffix: str = "") -> str:
    if not _SAFE_SLUG.fullmatch(owner or "") or not _SAFE_SLUG.fullmatch(repo or ""):
        raise RuntimeError("github_repo_slug_invalid")
    return f"/repos/{owner}/{repo}{suffix}"


def _root() -> Path:
    return Path(os.environ.get("URIRUN_PROJECTS", str(Path.home() / ".urirun-projects")))


def _name(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _git(args: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)


def _lease_github_token() -> str:
    vault_url = os.environ.get("URIRUN_VAULT_URL", "").rstrip("/")
    vault_token = os.environ.get("URIRUN_VAULT_TOKEN", "")
    entry_id = os.environ.get("GITHUB_VAULT_ENTRY_ID", "github-cli-runtime")
    if not vault_url or not vault_token:
        return ""
    body = json.dumps({"origin": "https://github.com", "field": "api_key"}).encode("utf-8")
    request = urllib.request.Request(
        f"{vault_url}/internal/vault/{urllib.parse.quote(entry_id, safe='')}/lease",
        data=body,
        method="POST",
        headers={"authorization": f"Bearer {vault_token}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError("github_vault_lease_failed") from error
    token = str(data.get("secret") or "")
    if not token:
        raise RuntimeError("github_vault_lease_failed")
    return token


def _gh(args: list[str], timeout: float = 120.0, *, use_vault: bool = True) -> subprocess.CompletedProcess:
    token = ""
    try:
        token = _lease_github_token() if use_vault and not os.environ.get("GH_TOKEN") else ""
        env = dict(os.environ)
        if token:
            env["GH_TOKEN"] = token
        return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout, env=env)
    except RuntimeError:
        return subprocess.CompletedProcess(["gh", *args], 1, "", "github_vault_lease_failed")
    finally:
        token = ""


def _github_identity(token: str, api_url: str = "https://api.github.com") -> dict[str, Any]:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/user",
        headers={"authorization": f"Bearer {token}", "accept": "application/vnd.github+json", "user-agent": "urirun-connector-github"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
            scopes = [item.strip() for item in response.headers.get("x-oauth-scopes", "").split(",") if item.strip()]
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError("github_token_validation_failed") from error
    return {"login": str(data.get("login") or ""), "scopes": scopes}


def _store_token_in_vault(*, vault_url: str, vault_token: str, entry_id: str, origin: str, token: str) -> str:
    if not vault_url or not vault_token:
        raise RuntimeError("github_vault_not_configured")
    body = json.dumps({
        "id": entry_id,
        "origin": origin,
        "label": "GitHub CLI token",
        "secrets": {"api_key": token},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{vault_url.rstrip('/')}/vault",
        data=body,
        method="POST",
        headers={"authorization": f"Bearer {vault_token}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError("github_vault_store_failed") from error
    return str(data.get("entry", {}).get("id") or entry_id)


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


@conn.handler("auth/query/status", isolated=True, meta={"label": "Verify GitHub API authentication"})
def auth_status(hostname: str = "github.com") -> dict[str, Any]:
    try:
        status, data = _api("GET", "/user")
    except RuntimeError as error:
        return urirun.fail(str(error), hostname=hostname, authenticated=False)
    if status != 200:
        return urirun.fail(f"github_auth_failed:{status}", hostname=hostname, authenticated=False)
    return urirun.ok(
        hostname=hostname,
        authenticated=True,
        login=str(data.get("login") or ""),
        account_type=str(data.get("type") or ""),
    )


@conn.handler("auth/command/import-to-vault", isolated=True, meta={"label": "Validate gh token and store it in vault"})
def import_gh_token_to_vault(
    hostname: str = "github.com",
    api_url: str = "https://api.github.com",
    vault_url: str = "",
    vault_entry_id: str = "github-cli-runtime",
) -> dict[str, Any]:
    """Move the active gh token into the vault without returning or logging it."""
    proc = _gh(["auth", "token", "--hostname", hostname], timeout=30, use_vault=False)
    token = proc.stdout.strip() if proc.returncode == 0 else ""
    if not token:
        return urirun.fail("github_cli_token_unavailable")
    try:
        identity = _github_identity(token, api_url)
        stored_id = _store_token_in_vault(
            vault_url=vault_url or os.environ.get("URIRUN_VAULT_URL", ""),
            vault_token=os.environ.get("URIRUN_VAULT_TOKEN", ""),
            entry_id=vault_entry_id,
            origin=f"https://{hostname}",
            token=token,
        )
    except RuntimeError as error:
        return urirun.fail(str(error))
    finally:
        token = ""
    return urirun.ok(
        hostname=hostname,
        login=identity["login"],
        scopes=identity["scopes"],
        vault_entry_id=stored_id,
        token_stored=True,
    )


@conn.handler("repo/command/create", isolated=True, meta={"label": "Create a GitHub repository with gh"})
def create_repo(
    name: str = "",
    owner: str = "",
    visibility: str = "private",
    description: str = "",
    source: str = "",
    push: bool = False,
    hostname: str = "github.com",
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return urirun.fail("invalid_repository_name")
    if owner and not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return urirun.fail("invalid_repository_owner")
    if visibility not in {"private", "public", "internal"}:
        return urirun.fail("invalid_repository_visibility")
    repository = f"{owner}/{name}" if owner else name
    args = ["repo", "create", repository, f"--{visibility}"]
    if description:
        args += ["--description", description[:350]]
    if source:
        args += ["--source", source, "--remote", "origin"]
        if push:
            args.append("--push")
    proc = _gh(args, timeout=180)
    if proc.returncode != 0:
        return urirun.fail("github_repository_create_failed", repository=repository)
    return urirun.ok(repository=repository, url=f"https://{hostname}/{repository}", created=True)


@conn.handler("repo/collaborator/command/invite", isolated=True, meta={"label": "Invite a least-privilege repository collaborator"})
def invite_collaborator(owner: str = "", repo: str = "", username: str = "", permission: str = "triage") -> dict[str, Any]:
    if not _SAFE_SLUG.fullmatch(username or "") or permission not in {"pull", "triage", "push", "maintain"}:
        return urirun.fail("github_collaborator_input_invalid")
    try:
        path = _repo_path(owner, repo, f"/collaborators/{username}")
    except RuntimeError as error:
        return urirun.fail(str(error))
    status, data = _api("PUT", path, {"permission": permission})
    if status not in {201, 204}:
        return urirun.fail(f"github_collaborator_invite_failed:{status}")
    return urirun.ok(
        owner=owner,
        repo=repo,
        username=username,
        permission=permission,
        invitation_id=data.get("id"),
        invited=status == 201,
        already_authorized=status == 204,
    )


@conn.handler("issue/command/create", isolated=True, meta={"label": "Create a governed GitHub issue"})
def create_issue(owner: str = "", repo: str = "", title: str = "", body: str = "", labels: list[str] | None = None, assignees: list[str] | None = None) -> dict[str, Any]:
    if not title.strip():
        return urirun.fail("github_issue_title_required")
    try:
        path = _repo_path(owner, repo, "/issues")
    except RuntimeError as error:
        return urirun.fail(str(error))
    payload = {
        "title": title[:256],
        "body": body[:60000],
        "labels": [str(item)[:100] for item in (labels or [])],
        "assignees": [str(item) for item in (assignees or []) if _SAFE_SLUG.fullmatch(str(item))],
    }
    status, data = _api("POST", path, payload)
    if status != 201:
        return urirun.fail(f"github_issue_create_failed:{status}")
    return urirun.ok(owner=owner, repo=repo, number=data.get("number"), url=data.get("html_url"), created=True)


@conn.handler("issue/command/assign", isolated=True, meta={"label": "Assign a governed GitHub issue"})
def assign_issue(owner: str = "", repo: str = "", number: int = 0, assignees: list[str] | None = None) -> dict[str, Any]:
    clean = [str(item) for item in (assignees or []) if _SAFE_SLUG.fullmatch(str(item))]
    if int(number or 0) < 1 or not clean:
        return urirun.fail("github_issue_assignment_invalid")
    try:
        path = _repo_path(owner, repo, f"/issues/{int(number)}/assignees")
    except RuntimeError as error:
        return urirun.fail(str(error))
    status, data = _api("POST", path, {"assignees": clean})
    if status != 201:
        return urirun.fail(f"github_issue_assign_failed:{status}")
    return urirun.ok(owner=owner, repo=repo, number=int(number), assignees=clean, url=data.get("html_url"))


# --- authoring surface -----------------------------------------------------

def urirun_bindings() -> dict[str, Any]:
    return conn.bindings()

@conn.handler("github://host/doctor/query/report", isolated=True, meta={"label": "Connector readiness report"})
def doctor() -> dict[str, Any]:
    """Return a safe, read-only connector readiness report for CI smoke tests."""
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "version": _connector_version(),
        "status": "ready",
    }


def _connector_version() -> str:
    try:
        from importlib.metadata import version

        return version("urirun-connector-github")
    except Exception:
        return "0.2.0"


def connector_manifest() -> dict[str, Any]:
    return conn.manifest(_urirun_compat.load_manifest(__package__))


def main(argv: list[str] | None = None) -> int:
    return conn.cli(argv, manifest_prose=_urirun_compat.load_manifest(__package__))


if __name__ == "__main__":
    raise SystemExit(main())
