from __future__ import annotations

import pytest

from forty8.config import load_settings
from forty8.orchestrator.engine import Orchestrator
from forty8.orchestrator.pipeline import placeholder_agents
from forty8.synth.generator import generate


@pytest.fixture
def settings(tmp_path):
    return load_settings(overrides={"storage": {
        "db_path": str(tmp_path / "forty8.db"), "mock_data_dir": str(tmp_path / "mock_data"),
    }})


@pytest.fixture
def make_orchestrator(settings):
    def _make(agents=None, real=False):
        """real=False starts from placeholder agents (orchestrator tests); real=True uses the built agents."""
        base = {} if real else placeholder_agents()
        return Orchestrator(settings, agents=base | (agents or {}))
    return _make


@pytest.fixture
def clean_submission(tmp_path, settings):
    return generate(tmp_path / "clean", profile="clean", mock_data_dir=settings.storage.mock_data_dir)


@pytest.fixture
def messy_submission(tmp_path, settings):
    return generate(tmp_path / "messy", profile="messy", mock_data_dir=settings.storage.mock_data_dir)
