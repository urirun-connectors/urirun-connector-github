# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json
import subprocess

import urirun
from urirun_connector_github import (
    auth_status, clone, connector_manifest, create_repo, import_gh_token_to_vault,
    install, list_repos, pull, repo_bindings, urirun_bindings,
)
import urirun_connector_github.core as core

ROUTES = {
    "github://host/repo/command/clone", "github://host/repo/command/pull",
    "github://host/repo/query/list", "github://host/package/command/install",
    "github://host/repo/query/bindings", "github://host/repo/command/create",
    "github://host/auth/query/status", "github://host/auth/command/import-to-vault",
    "github://host/doctor/query/report",
}


def test_clone_requires_url():
    assert clone("")["ok"] is False


def test_name_parsing():
    assert core._name("https://github.com/if-uri/urirun.git") == "urirun"
    assert core._name("https://github.com/if-uri/urirun-connector-llm") == "urirun-connector-llm"


def test_clone_runs_git(monkeypatch, tmp_path):
    calls = {}

    def fake_git(args, timeout=300.0):
        calls["args"] = args
        (tmp_path / "repo" / ".git").mkdir(parents=True)  # simulate a successful clone
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(core, "_git", fake_git)
    r = clone("https://github.com/if-uri/repo.git", dest=str(tmp_path / "repo"))
    assert r["ok"] is True and r["repo"].endswith("repo")
    assert calls["args"][0] == "clone"


def test_clone_is_idempotent(tmp_path):
    (tmp_path / "x" / ".git").mkdir(parents=True)
    r = clone("https://github.com/if-uri/x.git", dest=str(tmp_path / "x"))
    assert r["ok"] and r["existed"] is True


def test_list_repos(tmp_path):
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "b" / ".git").mkdir(parents=True)
    (tmp_path / "notrepo").mkdir()
    r = list_repos(str(tmp_path))
    assert r["count"] == 2 and all(p.endswith(("a", "b")) for p in r["repos"])


def test_repo_bindings_from_module(monkeypatch, tmp_path):
    def fake_run(cmd, **kw):
        # emulate `python -c "import M; print(urirun_bindings())"`
        doc = {"version": "urirun.bindings.v2", "bindings": {"x://host/a/query/b": {}}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(doc), "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    r = repo_bindings(dest=str(tmp_path), module="some_pkg")
    assert r["ok"] and "x://host/a/query/b" in r["bindings"]


def test_repo_bindings_from_file(tmp_path):
    doc = {"version": "urirun.bindings.v2", "bindings": {"y://host/c/query/d": {}}}
    (tmp_path / "thing.bindings.json").write_text(json.dumps(doc))
    r = repo_bindings(dest=str(tmp_path))
    assert r["ok"] and "y://host/c/query/d" in r["bindings"]


def test_auth_status_never_returns_token(monkeypatch):
    monkeypatch.setattr(core, "_gh", lambda args, timeout=120: subprocess.CompletedProcess(args, 0, "token-value", ""))
    result = auth_status()
    assert result["authenticated"] is True
    assert "token" not in result


def test_gh_uses_short_vault_lease_without_exposing_token(monkeypatch):
    calls = {}
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(core, "_lease_github_token", lambda: "short-lived-secret")

    def fake_run(command, **kwargs):
        calls.update(command=command, env=kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = core._gh(["repo", "list"])
    assert result.returncode == 0
    assert calls["env"]["GH_TOKEN"] == "short-lived-secret"
    assert "short-lived-secret" not in result.stdout + result.stderr


def test_import_gh_token_validates_and_stores_without_returning_secret(monkeypatch):
    monkeypatch.setattr(core, "_gh", lambda args, timeout=120, **kwargs: subprocess.CompletedProcess(args, 0, "secret-token\n", ""))
    monkeypatch.setattr(core, "_github_identity", lambda token, api_url: {"login": "founder", "scopes": ["repo"]})
    monkeypatch.setattr(core, "_store_token_in_vault", lambda **kwargs: "github-cli-runtime")
    result = import_gh_token_to_vault(vault_url="http://vault")
    assert result["ok"] and result["token_stored"] and result["login"] == "founder"
    assert "secret-token" not in json.dumps(result)


def test_create_repo_uses_gh_without_exposing_credentials(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(core, "_gh", lambda args, timeout=120: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    result = create_repo("urirun-connector-plesk", owner="urirun-connectors", visibility="public", source=str(tmp_path))
    assert result["ok"] and result["repository"] == "urirun-connectors/urirun-connector-plesk"
    assert calls[0][:3] == ["repo", "create", "urirun-connectors/urirun-connector-plesk"]


def test_bindings_compile_and_routes():
    doc = urirun_bindings()
    assert set(doc["bindings"]) == ROUTES
    registry = urirun.compile_registry(json.loads(json.dumps(doc)))
    assert ROUTES <= {r["uri"] for r in urirun.list_routes(registry)}


def test_manifest():
    m = connector_manifest()
    assert m["id"] == "github" and m["uriSchemes"] == ["github"]
    assert set(m["routes"]) == ROUTES
