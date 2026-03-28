"""Integration tests for Allure result merging across shards."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ALLURE_BIN = shutil.which("allure")

pytestmark = pytest.mark.skipif(ALLURE_BIN is None, reason="allure CLI is required for this test")


def _run_pytest_for_shard(
    test_root: Path, results_dir: Path, shard_id: int, num_shards: int
) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_root),
            f"--shard-id={shard_id}",
            f"--num-shards={num_shards}",
            f"--alluredir={results_dir}",
            "-q",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _load_allure_result_names(results_dir: Path) -> set[str]:
    result_names: set[str] = set()
    for result_file in results_dir.glob("*-result.json"):
        result = json.loads(result_file.read_text())
        result_names.add(result["fullName"])
    return result_names


def test_allure_results_from_multiple_shards_merge_into_one_report(tmp_path: Path) -> None:
    test_root = tmp_path / "sample_tests"
    test_root.mkdir()
    (test_root / "test_alpha.py").write_text(
        "\n".join(
            [
                "def test_alpha_one():",
                "    assert True",
                "",
                "def test_alpha_two():",
                "    assert True",
                "",
            ]
        )
    )
    (test_root / "test_beta.py").write_text(
        "\n".join(
            [
                "def test_beta_one():",
                "    assert True",
                "",
                "def test_beta_two():",
                "    assert True",
                "",
            ]
        )
    )

    expected_result_names = {
        "test_alpha#test_alpha_one",
        "test_alpha#test_alpha_two",
        "test_beta#test_beta_one",
        "test_beta#test_beta_two",
    }
    results_dir = tmp_path / "allure-results"
    report_dir = tmp_path / "allure-report"

    _run_pytest_for_shard(test_root, results_dir, shard_id=0, num_shards=2)
    _run_pytest_for_shard(test_root, results_dir, shard_id=1, num_shards=2)

    assert _load_allure_result_names(results_dir) == expected_result_names

    subprocess.run(
        [ALLURE_BIN, "generate", str(results_dir), "-o", str(report_dir), "--clean"],
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads((report_dir / "widgets" / "summary.json").read_text())
    assert summary["statistic"]["total"] == len(expected_result_names)
    assert summary["statistic"]["passed"] == len(expected_result_names)
