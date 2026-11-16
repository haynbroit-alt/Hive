import asyncio
import copy

import pytest

from hive.config import DEFAULTS, build_hive


def make_hive(tmp_path, **policy):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["providers"] = {"offline": {"type": "offline"}}
    cfg["_tmp_dir"] = str(tmp_path)
    cfg["policy"].update(policy)
    cfg["studio"].update(width=320, height=180, fps=8)  # rapide en test, mêmes contrôles qu'en vrai
    return build_hive(cfg, in_memory=True)


@pytest.fixture
def hive(tmp_path):
    return make_hive(tmp_path)


def run(coro):
    return asyncio.run(coro)
