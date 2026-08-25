# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import urirun
from urirun_connector_github import (
    account_query_twin, assign_issue, auth_status, clone, connector_manifest, create_issue,
    create_repo, dispatch_initial_ref_validation, import_gh_token_to_vault, install,
    invite_collaborator, list_issues, list_repos, load_initial_ref_validation_receipt,
    observe_initial_ref_validation, pull, repo_bindings, urirun_bindings,
)
import urirun_connector_github.core as core

ROUTES = {
    "github://host/repo/command/clone", "github://host/repo/command/pull",
    "github://host/repo/query/list", "github://host/package/command/install",
    "github://host/repo/query/bindings", "github://host/repo/command/create",
    "github://host/auth/query/status", "github://host/auth/command/import-to-vault",
    "github://host/account/query/twin",
    "github://host/repo/collaborator/command/invite", "github://host/issue/command/create",
    "github://host/issue/command/assign", "github://host/issue/query/list",
    "github://host/validator/initial-ref/command/dispatch",
    "github://host/validator/initial-ref/query/run",
    "github://host/validator/initial-ref/query/receipt",
    "github://host/doctor/query/report",
}

WORKFLOW_SHA = "a" * 40
HEAD_SHA = "c" * 40
PLAN_DIGEST = "d" * 64
PUBLICATION_DIGEST = "e" * 64
INITIAL_REF_REQUEST = {
    "schema": "subactor.repository-initial-ref-validation-dispatch/v1",
    "handoffId": "b" * 64,
    "repository": "subactor/example",
    "repositoryOwner": "subactor",
    "repositoryName": "example",
    "validatorRepository": "subactor/validator-agent",
    "workflow": "validator.yml",
    "workflowPath": ".github/workflows/validator.yml",
    "workflowRef": "main",
    "displayTitle": f"repository-initial-ref subactor/example {HEAD_SHA} initial-ref-example",
    "expectedPlanDigest": PLAN_DIGEST,
    "expectedPublicationReceiptDigest": PUBLICATION_DIGEST,
    "expectedHeadSha": HEAD_SHA,
    "correlationId": "initial-ref-example",
    "inputs": {
        "strategy": "repository-initial-ref",
        "repository_owner": "subactor",
        "repository_name": "example",
        "expected_head_sha": HEAD_SHA,
        "expected_plan_digest": PLAN_DIGEST,
        "expected_publication_receipt_digest": PUBLICATION_DIGEST,
        "initial_ref_plan_b64": "e30=",
        "initial_ref_grant_b64": "e30=",
        "initial_ref_publication_receipt_b64": "e30=",
        "correlation_id": "initial-ref-example",
        "execution_profile": "production",
        "force": True,
    },
}


def initial_ref_run(**overrides):
    return {
        "id": 32900000001,
        "display_title": INITIAL_REF_REQUEST["displayTitle"],
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": WORKFLOW_SHA,
        "path": ".github/workflows/validator.yml",
        "status": "in_progress",
        "conclusion": None,
        "html_url": "https://github.test/run/32900000001",
        **overrides,
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
    monkeypatch.setattr(core, "_api", lambda method, path: (200, {"login": "bot", "type": "Bot"}))
    result = auth_status()
    assert result["authenticated"] is True
    assert "token" not in result


def test_github_token_resolves_only_a_reference(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("GITHUB_TOKEN_REF", "getv://GITHUB_TOKEN")
    assert core._secret_reference(
        "GITHUB_TOKEN_REF", core._GITHUB_TOKEN_REF, "github_token"
    ) == "github-secret"
    monkeypatch.setenv("GITHUB_TOKEN_REF", "literal-token")
    try:
        core._secret_reference("GITHUB_TOKEN_REF", core._GITHUB_TOKEN_REF, "github_token")
    except RuntimeError as error:
        assert str(error) == "github_token_ref_invalid"
    else:
        raise AssertionError("literal token reference must be rejected")


def test_api_token_prefers_auditable_vault_lease(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core,
        "_lease_github_token",
        lambda **context: calls.append(("vault", context)) or "leased-token",
    )
    monkeypatch.setattr(
        core,
        "_secret_reference",
        lambda *args, **kwargs: calls.append(("env", {})) or "environment-token",
    )

    assert core._token(purpose="github.issue.query", target="provider:github") == "leased-token"
    assert calls == [("vault", {"purpose": "github.issue.query", "target": "provider:github"})]


def test_api_token_uses_declared_environment_reference_only_without_vault(monkeypatch):
    monkeypatch.setattr(core, "_lease_github_token", lambda **context: "")
    monkeypatch.setattr(core, "_secret_reference", lambda *args, **kwargs: "environment-token")
    assert core._token() == "environment-token"


def test_gh_uses_short_vault_lease_without_exposing_token(monkeypatch):
    calls = {}
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(core, "_lease_github_token", lambda **context: "short-lived-secret")

    def fake_run(command, **kwargs):
        calls.update(command=command, env=kwargs["env"])
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = core._gh(["repo", "list"])
    assert result.returncode == 0
    assert calls["env"]["GH_TOKEN"] == "short-lived-secret"
    assert "short-lived-secret" not in result.stdout + result.stderr


def test_gh_initial_ref_lease_has_fixed_purpose_and_target(monkeypatch):
    leases = []
    monkeypatch.setattr(
        core,
        "_lease_github_token",
        lambda **context: leases.append(context) or "short-lived-secret",
    )
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "ok", ""),
    )

    result = core._initial_ref_gh(["workflow", "list"], timeout=30)

    assert result.returncode == 0
    assert leases == [{
        "purpose": "github.validator.initial-ref",
        "target": "repository:subactor--validator-agent",
    }]


def test_import_gh_token_validates_and_stores_without_returning_secret(monkeypatch):
    monkeypatch.setattr(core, "_gh", lambda args, timeout=120, **kwargs: subprocess.CompletedProcess(args, 0, "secret-token\n", ""))
    monkeypatch.setattr(core, "_github_identity", lambda token, api_url: {"login": "founder", "scopes": ["repo"]})
    monkeypatch.setattr(core, "_store_token_in_vault", lambda **kwargs: "github-cli-runtime")
    result = import_gh_token_to_vault(vault_url="http://vault")
    assert result["ok"] and result["token_stored"] and result["login"] == "founder"
    assert "secret-token" not in json.dumps(result)


def test_import_gh_token_rejects_excessive_scopes_before_vault_write(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "_gh", lambda args, timeout=120, **kwargs: subprocess.CompletedProcess(args, 0, "secret-token\n", ""))
    monkeypatch.setattr(
        core,
        "_github_identity",
        lambda token, api_url: {"login": "founder", "scopes": ["repo", "admin:org", "delete_repo"]},
    )
    monkeypatch.setattr(core, "_store_token_in_vault", lambda **kwargs: writes.append(kwargs))

    result = import_gh_token_to_vault(vault_url="http://vault")

    assert result["ok"] is False
    assert result["error"] == "github_token_scope_excessive"
    assert writes == []
    assert "secret-token" not in json.dumps(result)


def test_import_gh_token_rejects_unverifiable_scopes(monkeypatch):
    monkeypatch.setattr(core, "_gh", lambda args, timeout=120, **kwargs: subprocess.CompletedProcess(args, 0, "secret-token\n", ""))
    monkeypatch.setattr(core, "_github_identity", lambda token, api_url: {"login": "founder", "scopes": []})

    result = import_gh_token_to_vault(vault_url="http://vault")

    assert result["ok"] is False
    assert result["error"] == "github_token_scopes_unverifiable"


def test_bootstrap_scope_policy_is_closed_and_environment_owned(monkeypatch):
    assert core._validate_bootstrap_scopes(["workflow", "repo", "repo"]) == ["repo", "workflow"]
    monkeypatch.setenv("GITHUB_BOOTSTRAP_ALLOWED_SCOPES", "repo,invalid scope")
    try:
        core._validate_bootstrap_scopes(["repo"])
    except RuntimeError as error:
        assert str(error) == "github_bootstrap_scope_policy_invalid"
    else:
        raise AssertionError("invalid bootstrap scope policy must fail closed")


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
    assert "github.com/urirun-connectors/" in m["install"]["pipSpec"]


def test_api_operations_are_structured_and_least_privilege(monkeypatch):
    calls=[]
    def fake_api(method,path,body=None):
        calls.append((method,path,body))
        if path=="/user": return 200,{"login":"bot","type":"Bot"}
        if path.endswith("/collaborators/intern"): return 201,{"id":7}
        if path.endswith("/assignees"): return 201,{"html_url":"https://example/issues/3"}
        if path.endswith("/issues"): return 201,{"number":3,"html_url":"https://example/issues/3"}
        return 201,{"html_url":"https://example/repo"}
    monkeypatch.setattr(core,"_api",fake_api)
    assert auth_status()["authenticated"]
    assert invite_collaborator(owner="org",repo="sandbox",username="intern",permission="triage")["invited"]
    assert create_issue(owner="org",repo="sandbox",title="First task",labels=["education"])["number"]==3
    assert assign_issue(owner="org",repo="sandbox",number=3,assignees=["intern"])["ok"]
    assert calls[1][2]["permission"]=="triage"


def test_issue_query_is_bounded_excludes_pull_requests_and_preserves_admission_metadata(monkeypatch):
    calls = []

    def fake_api(method, path, body=None, query=None, **context):
        calls.append((method, path, query, context))
        return 200, [
            {
                "number": 7,
                "node_id": "I_fixture",
                "title": "Implement bounded ingestion",
                "body": "No credentials here",
                "state": "open",
                "html_url": "https://github.com/subactor/core/issues/7",
                "user": {"login": "maintainer"},
                "author_association": "MEMBER",
                "labels": [{"name": "subactor:autonomy"}],
                "assignees": [{"login": "automation-bot"}],
                "created_at": "2026-08-25T10:00:00Z",
                "updated_at": "2026-08-25T11:00:00Z",
            },
            {
                "number": 8,
                "title": "A pull request",
                "pull_request": {"url": "https://api.github.com/pulls/8"},
            },
        ]

    monkeypatch.setattr(core, "_api", fake_api)
    result = list_issues(owner="subactor", repo="core", labels=["subactor:autonomy"], limit=10)
    assert result["ok"] and result["count"] == 1 and result["complete"] is True
    assert result["mutation_attempted"] is False
    assert result["issues"][0]["author_association"] == "MEMBER"
    assert result["issues"][0]["labels"] == ["subactor:autonomy"]
    assert calls == [("GET", "/repos/subactor/core/issues", {
        "state": "open", "sort": "updated", "direction": "desc",
        "page": 1, "per_page": 100, "labels": "subactor:autonomy",
    }, {"purpose": "github.issue.query", "target": "provider:github"})]


def test_issue_query_rejects_unbounded_or_ambiguous_input(monkeypatch):
    monkeypatch.setattr(core, "_api", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call API")))
    assert list_issues(owner="subactor", repo="core", limit=501)["ok"] is False
    assert list_issues(owner="subactor", repo="core", state="pending")["ok"] is False
    assert list_issues(owner="subactor", repo="core", since="yesterday")["ok"] is False


def test_collaborator_rejects_admin_permission():
    assert invite_collaborator(owner="org",repo="repo",username="intern",permission="admin")["ok"] is False


def test_account_twin_maps_organizations_and_repositories(monkeypatch):
    def fake_api(method, path, body=None, query=None):
        if path == "/user":
            return 200, {"id": 1, "login": "tom", "name": "Tom", "type": "User"}
        if path == "/user/orgs":
            return 200, [{"id": 2, "login": "subactor", "html_url": "https://github.com/subactor"}]
        if path == "/user/repos":
            return 200, [{"id": 3, "full_name": "subactor/core", "default_branch": "main",
                          "visibility": "private", "organization": {"id": 2}, "has_issues": True}]
        raise AssertionError(path)

    monkeypatch.setattr(core, "_api", fake_api)
    result = account_query_twin()
    assert result["ok"] and result["counts"] == {"scopes": 2, "repositories": 1}
    assert result["twin_fact"]["twin_type"] == "forge.account"
    assert result["mutation_attempted"] is False


def test_initial_ref_dispatch_is_fixed_and_deduplicated(monkeypatch):
    commands = []
    monkeypatch.setattr(core, "_initial_ref_workflow_sha", lambda: WORKFLOW_SHA)
    monkeypatch.setattr(
        core,
        "_initial_ref_runs",
        lambda request, workflow_sha: [initial_ref_run()],
    )
    monkeypatch.setattr(
        core,
        "_initial_ref_gh",
        lambda args, timeout: commands.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = dispatch_initial_ref_validation(INITIAL_REF_REQUEST)

    assert result["ok"] and result["status"] == "deduplicated"
    assert result["workflowSha"] == WORKFLOW_SHA
    assert result["run"]["id"] == 32900000001
    assert commands == []


def test_initial_ref_dispatch_uses_only_fixed_workflow_and_sorted_inputs(monkeypatch):
    commands = []
    monkeypatch.setattr(core, "_initial_ref_workflow_sha", lambda: WORKFLOW_SHA)
    monkeypatch.setattr(core, "_initial_ref_runs", lambda request, workflow_sha: [])
    monkeypatch.setattr(
        core,
        "_initial_ref_gh",
        lambda args, timeout: commands.append((args, timeout))
        or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = dispatch_initial_ref_validation(INITIAL_REF_REQUEST)

    assert result == {"ok": True, "status": "dispatched", "workflowSha": WORKFLOW_SHA, "run": None}
    args, timeout = commands[0]
    assert args[:7] == [
        "workflow", "run", "validator.yml", "--repo", "subactor/validator-agent", "--ref", "main",
    ]
    assert timeout == 120
    fields = [args[index + 1] for index, value in enumerate(args) if value == "-f"]
    assert fields == sorted(fields)
    assert "force=true" in fields


def test_initial_ref_rejects_selectable_validator_binding_before_transport(monkeypatch):
    monkeypatch.setattr(
        core,
        "_initial_ref_workflow_sha",
        lambda: (_ for _ in ()).throw(AssertionError("must not query GitHub")),
    )
    request = {**INITIAL_REF_REQUEST, "validatorRepository": "attacker/repository"}

    result = dispatch_initial_ref_validation(request)

    assert result["ok"] is False
    assert result["error"] == "initial_ref_request_invalid"
    assert "attacker" not in json.dumps(result)


def test_initial_ref_observation_returns_only_exact_run(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core,
        "_initial_ref_runs",
        lambda request, workflow_sha: calls.append((request, workflow_sha)) or [initial_ref_run()],
    )

    result = observe_initial_ref_validation(INITIAL_REF_REQUEST, WORKFLOW_SHA)

    assert result["ok"] and result["run"]["id"] == 32900000001
    assert calls == [(INITIAL_REF_REQUEST, WORKFLOW_SHA)]
    assert observe_initial_ref_validation(INITIAL_REF_REQUEST, "not-a-sha")["ok"] is False


def test_initial_ref_run_query_is_bounded_and_exact(monkeypatch):
    calls = []

    def fake_api(method, path, body=None, query=None, **context):
        calls.append((method, path, query, context))
        return 200, {"workflow_runs": [
            initial_ref_run(),
            initial_ref_run(id=32900000002, display_title="different handoff"),
        ]}

    monkeypatch.setattr(core, "_api", fake_api)

    matches = core._initial_ref_runs(INITIAL_REF_REQUEST, WORKFLOW_SHA)

    assert [run["id"] for run in matches] == [32900000001]
    assert calls == [(
        "GET",
        "/repos/subactor/validator-agent/actions/workflows/validator.yml/runs",
        {"branch": "main", "event": "workflow_dispatch", "per_page": 100},
        {
            "purpose": "github.validator.initial-ref",
            "target": "repository:subactor--validator-agent",
        },
    )]


def test_initial_ref_run_query_rejects_ambiguous_identity(monkeypatch):
    monkeypatch.setattr(
        core,
        "_initial_ref_api",
        lambda method, path, query=None: (200, {
            "workflow_runs": [initial_ref_run(), initial_ref_run(id=32900000002)],
        }),
    )

    try:
        core._initial_ref_runs(INITIAL_REF_REQUEST, WORKFLOW_SHA)
    except RuntimeError as error:
        assert str(error) == "initial_ref_run_ambiguous"
    else:
        raise AssertionError("ambiguous Validator run identity must fail closed")


def test_initial_ref_receipt_downloads_fixed_artifact_and_verifies_attestation(monkeypatch):
    commands = []
    monkeypatch.setattr(
        core,
        "_initial_ref_run",
        lambda request, workflow_sha, run_id: initial_ref_run(status="completed", conclusion="success"),
    )

    def fake_gh(args, timeout):
        commands.append((args, timeout))
        if args[:2] == ["run", "download"]:
            target = Path(args[args.index("--dir") + 1]) / "validator-initial-ref-receipt.json"
            target.write_text(json.dumps({"schema": "wellmanifest.repository-initial-ref/v1"}))
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps([{"verificationResult": "success"}]), "")

    monkeypatch.setattr(core, "_initial_ref_gh", fake_gh)

    result = load_initial_ref_validation_receipt(
        INITIAL_REF_REQUEST,
        runId=32900000001,
        workflowSha=WORKFLOW_SHA,
    )

    assert result["ok"] and result["receipt"]["schema"] == "wellmanifest.repository-initial-ref/v1"
    assert result["attestation"] == {
        "verified": True,
        "repository": "subactor/validator-agent",
        "signerWorkflow": "subactor/validator-agent/.github/workflows/validator.yml",
        "sourceRef": "refs/heads/main",
        "sourceDigest": WORKFLOW_SHA,
        "statementCount": 1,
    }
    assert commands[0][0][:7] == [
        "run", "download", "32900000001", "--repo", "subactor/validator-agent", "--name", "validator-agent-result",
    ]
    assert commands[1][0][:4] == ["attestation", "verify", commands[1][0][2], "--repo"]
    assert "--deny-self-hosted-runners" in commands[1][0]


def test_initial_ref_receipt_fails_closed_without_attestation(monkeypatch):
    monkeypatch.setattr(
        core,
        "_initial_ref_run",
        lambda request, workflow_sha, run_id: initial_ref_run(status="completed"),
    )

    def fake_gh(args, timeout):
        if args[:2] == ["run", "download"]:
            target = Path(args[args.index("--dir") + 1]) / "validator-initial-ref-receipt.json"
            target.write_text("{}")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "opaque-sensitive-detail")

    monkeypatch.setattr(core, "_initial_ref_gh", fake_gh)

    result = load_initial_ref_validation_receipt(
        INITIAL_REF_REQUEST,
        runId=32900000001,
        workflowSha=WORKFLOW_SHA,
    )

    assert result["ok"] is False
    assert result["error"] == "initial_ref_attestation_invalid"
    assert "opaque-sensitive-detail" not in json.dumps(result)
