# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json
import subprocess

import urirun
from urirun_connector_github import (
    clone, connector_manifest, install, list_repos, pull, repo_bindings, urirun_bindings,
)
import urirun_connector_github.core as core

ROUTES = {
    "github://host/repo/command/clone", "github://host/repo/command/pull",
    "github://host/repo/query/list", "github://host/package/command/install",
    "github://host/repo/query/bindings",
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


def test_bindings_compile_and_routes():
    doc = urirun_bindings()
    assert set(doc["bindings"]) == ROUTES
    registry = urirun.compile_registry(json.loads(json.dumps(doc)))
    assert ROUTES <= {r["uri"] for r in urirun.list_routes(registry)}


def test_manifest():
    m = connector_manifest()
    assert m["id"] == "github" and m["uriSchemes"] == ["github"]
    assert set(m["routes"]) == ROUTES
