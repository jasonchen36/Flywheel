from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LEARNING = ROOT / "learning"
sys.path.insert(0, str(LEARNING))

import surface_gate
from surface_gate import PolicyError, is_allowed, load_rules, main


def _policy(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "editable_surfaces.json"
    path.write_text(json.dumps(value))
    return path


def test_load_rules_normalizes_strings_objects_and_actor_lists(tmp_path: Path):
    path = _policy(
        tmp_path,
        {
            "allow": [
                str(tmp_path / "allowed" / "*"),
                {"glob": str(tmp_path / "owned" / "*"), "who": ["worker.py"]},
                {"path": str(tmp_path / "exact.md")},
            ],
            "deny": [str(tmp_path / "allowed" / "secret"), {"glob": str(tmp_path / "denied" / "*")}],
        },
    )
    rules = load_rules(path)
    assert rules["allow"][0] == {"glob": str(tmp_path / "allowed" / "*")}
    assert rules["allow"][1]["who"] == ["worker.py"]
    assert rules["allow"][2] == {"glob": str(tmp_path / "exact.md")}
    assert rules["deny"][1] == {"glob": str(tmp_path / "denied" / "*")}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "must be a JSON object"),
        ({"allow": {}, "deny": []}, "must be lists"),
        ({"allow": [], "deny": {}}, "must be lists"),
        ({"allow": [""], "deny": []}, "must not be empty"),
        ({"allow": [1], "deny": []}, "must be strings or JSON objects"),
        ({"allow": [{}], "deny": []}, "glob or path must be a non-empty string"),
        ({"allow": [{"glob": 1}], "deny": []}, "glob or path must be a non-empty string"),
        ({"allow": [{"glob": "/tmp/*", "who": "worker.py"}], "deny": []}, "who must be a list"),
        ({"allow": [{"glob": "/tmp/*", "who": [1]}], "deny": []}, "who must be a list"),
    ],
)
def test_load_rules_rejects_invalid_schema(tmp_path: Path, value: object, message: str):
    with pytest.raises(PolicyError, match=message):
        load_rules(_policy(tmp_path, value))


def test_load_rules_reports_missing_malformed_and_unreadable_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing = tmp_path / "missing.json"
    with pytest.raises(PolicyError, match="not found"):
        load_rules(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    with pytest.raises(PolicyError, match="cannot read"):
        load_rules(malformed)

    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text("{}")

    def fail_read(_self: Path) -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(PolicyError, match="permission denied"):
        load_rules(unreadable)


def test_is_allowed_is_fail_closed_with_deny_precedence_and_actor_scope(tmp_path: Path):
    rules = {
        "allow": [
            {"glob": str(tmp_path / "allowed" / "*")},
            {"glob": str(tmp_path / "owned" / "*"), "who": ["worker.py"]},
        ],
        "deny": [{"glob": str(tmp_path / "allowed" / "secret*")}],
    }
    assert is_allowed(str(tmp_path / "allowed" / "file.md"), rules) is True
    assert is_allowed(str(tmp_path / "allowed" / "secret.md"), rules) is False
    assert is_allowed(str(tmp_path / "owned" / "file.md"), rules, "worker.py") is True
    assert is_allowed(str(tmp_path / "owned" / "file.md"), rules, "other.py") is False
    assert is_allowed(str(tmp_path / "unknown" / "file.md"), rules) is False


def test_main_supports_paths_proposal_and_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    allowed = tmp_path / "allowed" / "file.md"
    denied = tmp_path / "denied" / "file.md"
    monkeypatch.setattr(surface_gate, "SURFACES", _policy(tmp_path, {"allow": [str(tmp_path / "allowed" / "*")], "deny": []}))

    assert main([str(allowed)], stdin=io.StringIO()) == 0
    assert "ALLOW" in capsys.readouterr().out

    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps({"paths": [str(denied)]}))
    assert main(["--file", str(proposal)], stdin=io.StringIO()) == 1
    captured = capsys.readouterr()
    assert "DENY" in captured.out
    assert "1 denied path" in captured.err

    assert main([], stdin=io.StringIO(str(allowed))) == 0
    assert "all 1 path" in capsys.readouterr().out


class _TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_main_handles_no_paths_and_policy_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    assert main([], stdin=_TerminalInput()) == 0
    assert "no paths" in capsys.readouterr().err

    monkeypatch.setattr(surface_gate, "SURFACES", tmp_path / "missing.json")
    assert main(["/tmp/file"], stdin=io.StringIO()) == 2
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"paths": "not-a-list"},
        {"paths": [""]},
        {"paths": [1]},
    ],
)
def test_main_rejects_invalid_proposal_shapes(tmp_path: Path, value: object, capsys: pytest.CaptureFixture[str]):
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps(value))
    assert main(["--file", str(proposal)], stdin=io.StringIO()) == 2
    assert "proposal" in capsys.readouterr().err


def test_main_reports_malformed_and_unreadable_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    proposal = tmp_path / "proposal.json"
    proposal.write_text("{")
    assert main(["--file", str(proposal)], stdin=io.StringIO()) == 2
    assert "cannot read proposal" in capsys.readouterr().err

    def fail_read(_self: Path) -> str:
        raise OSError("unreadable")

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert main(["--file", str(proposal)], stdin=io.StringIO()) == 2
    assert "unreadable" in capsys.readouterr().err


def test_shipped_policy_remaps_default_roots_to_custom_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    home = tmp_path / "home"
    harness = tmp_path / "custom-harness"
    pi_skills = tmp_path / "custom-pi-skills"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(surface_gate, "HARNESS_HOME", harness)
    monkeypatch.setattr(surface_gate, "PI_SKILLS", pi_skills)
    rules = load_rules(ROOT / "config" / "editable_surfaces.example.json")

    assert is_allowed(
        str(harness / "MEMORY" / "lessons" / "lesson_autogen_example.md"),
        rules,
        "self_improve.py",
    ) is True
    assert is_allowed(
        str(pi_skills / "review" / "SKILL.md"),
        rules,
        "skill_autofix.py",
    ) is True
    assert is_allowed(str(harness / "hooks" / "unsafe.ts"), rules) is False
    assert surface_gate._expanded_pattern(str(tmp_path / "unrelated" / "*")) == str(
        tmp_path / "unrelated" / "*"
    )
