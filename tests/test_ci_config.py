"""The deploy workflow must never run on push again (it used to reset --hard on EC2 with no tests)."""
from pathlib import Path

import yaml


def test_deploy_is_manual_and_locked():
    wf = yaml.safe_load(Path(".github/workflows/deploy.yml").read_text(encoding="utf-8"))
    triggers = wf.get("on", wf.get(True))  # PyYAML parses the bare key `on` as True
    assert "push" not in triggers
    job = wf["jobs"]["deploy"]
    assert job.get("if") is False or "ci" in (job.get("needs") or [])
