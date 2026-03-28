"""Test pytest_shard.pytest_shard."""

import collections
import itertools
import json
from types import SimpleNamespace

from hypothesis import given, strategies
import pytest

from pytest_shard import pytest_shard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MockItem = collections.namedtuple("MockItem", "nodeid")
MockReport = collections.namedtuple("MockReport", ["when", "nodeid", "duration"])


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


@given(strategies.integers(min_value=1))
def test_positive_int_with_pos(x):
    assert pytest_shard.positive_int(x) == x
    assert pytest_shard.positive_int(str(x)) == x


def test_non_negative_int_accepts_zero():
    assert pytest_shard.non_negative_int(0) == 0
    assert pytest_shard.non_negative_int("0") == 0


@given(strategies.integers(max_value=0))
def test_positive_int_with_neg(x):
    with pytest.raises(ValueError):
        pytest_shard.positive_int(x)
    with pytest.raises(ValueError):
        pytest_shard.positive_int(str(x))


def test_positive_int_with_non_num():
    invalid = ["foobar", "x1", "1x"]
    for s in invalid:
        with pytest.raises(ValueError):
            pytest_shard.positive_int(s)


# ---------------------------------------------------------------------------
# sha256hash
# ---------------------------------------------------------------------------


@given(strategies.text())
def test_sha256hash_deterministic(s):
    x = pytest_shard.sha256hash(s)
    y = pytest_shard.sha256hash(s)
    assert x == y
    assert isinstance(x, int)



# ---------------------------------------------------------------------------
# Hash mode: filter_items_by_shard
# ---------------------------------------------------------------------------


@given(
    names=strategies.lists(strategies.text(), unique=True),
    num_shards=strategies.integers(min_value=1, max_value=500),
)
def test_filter_items_by_shard_covers_all(names, num_shards):
    items = [MockItem(name) for name in names]
    filtered = [
        pytest_shard.filter_items_by_shard(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    all_filtered = list(itertools.chain(*filtered))
    assert len(all_filtered) == len(items)
    assert set(all_filtered) == set(items)


# ---------------------------------------------------------------------------
# Round-robin mode: filter_items_round_robin
# ---------------------------------------------------------------------------


@given(
    names=strategies.lists(strategies.text(), unique=True),
    num_shards=strategies.integers(min_value=1, max_value=500),
)
def test_filter_items_round_robin_covers_all(names, num_shards):
    items = [MockItem(name) for name in names]
    filtered = [
        pytest_shard.filter_items_round_robin(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    all_filtered = list(itertools.chain(*filtered))
    assert len(all_filtered) == len(items)
    assert set(all_filtered) == set(items)


@given(
    names=strategies.lists(strategies.text(), unique=True, min_size=1),
    num_shards=strategies.integers(min_value=1, max_value=500),
)
def test_filter_items_round_robin_balanced(names, num_shards):
    items = [MockItem(name) for name in names]
    counts = [
        len(pytest_shard.filter_items_round_robin(items, shard_id=i, num_shards=num_shards))
        for i in range(num_shards)
    ]
    assert max(counts) - min(counts) <= 1


def test_filter_items_round_robin_order_is_stable():
    """Same inputs always produce the same shard assignment."""
    items = [MockItem(f"test_{i}") for i in range(10)]
    first = pytest_shard.filter_items_round_robin(items, shard_id=0, num_shards=3)
    second = pytest_shard.filter_items_round_robin(items, shard_id=0, num_shards=3)
    assert first == second


# ---------------------------------------------------------------------------
# Duration mode: load_durations + filter_items_by_duration
# ---------------------------------------------------------------------------


def test_load_durations(tmp_path):
    data = {"tests/test_foo.py::test_a": 1.5, "tests/test_foo.py::test_b": 0.3}
    path = tmp_path / ".test_durations"
    path.write_text(json.dumps(data))
    assert pytest_shard.load_durations(path) == data


def test_filter_items_by_duration_covers_all():
    items = [MockItem(f"test_{i}") for i in range(9)]
    durations = {f"test_{i}": float(i + 1) for i in range(9)}
    num_shards = 3
    filtered = [
        pytest_shard.filter_items_by_duration(
            items, shard_id=i, num_shards=num_shards, durations=durations
        )
        for i in range(num_shards)
    ]
    all_filtered = list(itertools.chain(*filtered))
    assert len(all_filtered) == len(items)
    assert set(all_filtered) == set(items)


def test_filter_items_by_duration_balances_time():
    """LPT bin-packing should produce more balanced total times than naive assignment."""
    # 9 tests with durations 1..9, split into 3 shards
    # Optimal: (9,6,3)=18, (8,5,2)=15, (7,4,1)=12 → max shard = 18
    # Naive sequential: (1,2,3)=6, (4,5,6)=15, (7,8,9)=24 → max shard = 24
    items = [MockItem(f"test_{i}") for i in range(1, 10)]
    durations = {f"test_{i}": float(i) for i in range(1, 10)}
    shard_totals = []
    for shard_id in range(3):
        assigned = pytest_shard.filter_items_by_duration(
            items, shard_id=shard_id, num_shards=3, durations=durations
        )
        shard_totals.append(sum(durations[item.nodeid] for item in assigned))
    assert max(shard_totals) <= 18.0  # LPT guarantees at most the optimal max


def test_filter_items_by_duration_uses_default_for_unknown():
    items = [MockItem("known"), MockItem("unknown")]
    durations = {"known": 5.0}
    # With default_duration=1.0, "known"(5s) goes to shard 0, "unknown"(1s) to shard 1
    shard0 = pytest_shard.filter_items_by_duration(items, 0, 2, durations, default_duration=1.0)
    shard1 = pytest_shard.filter_items_by_duration(items, 1, 2, durations, default_duration=1.0)
    assert MockItem("known") in shard0
    assert MockItem("unknown") in shard1


# ---------------------------------------------------------------------------
# _DurationRecorderPlugin
# ---------------------------------------------------------------------------


def test_duration_recorder_writes_file(tmp_path):
    path = tmp_path / ".test_durations"
    plugin = pytest_shard._DurationRecorderPlugin(path)

    plugin.pytest_runtest_logreport(
        MockReport(when="call", nodeid="tests/test_foo.py::test_a", duration=1.5)
    )
    plugin.pytest_runtest_logreport(
        MockReport(when="call", nodeid="tests/test_foo.py::test_b", duration=0.3)
    )
    plugin.pytest_sessionfinish(SimpleNamespace(), 0)

    assert json.loads(path.read_text()) == {
        "tests/test_foo.py::test_a": 1.5,
        "tests/test_foo.py::test_b": 0.3,
    }


def test_duration_recorder_ignores_non_call_phases(tmp_path):
    path = tmp_path / ".test_durations"
    plugin = pytest_shard._DurationRecorderPlugin(path)

    for phase in ("setup", "teardown"):
        plugin.pytest_runtest_logreport(
            MockReport(when=phase, nodeid="tests/test_foo.py::test_a", duration=1.0)
        )

    plugin.pytest_sessionfinish(SimpleNamespace(), 0)
    assert json.loads(path.read_text()) == {}


def test_duration_recorder_merges_with_existing(tmp_path):
    path = tmp_path / ".test_durations"
    path.write_text(json.dumps({"tests/test_foo.py::test_a": 1.0}))

    plugin = pytest_shard._DurationRecorderPlugin(path)
    plugin.pytest_runtest_logreport(
        MockReport(when="call", nodeid="tests/test_foo.py::test_b", duration=2.0)
    )
    plugin.pytest_sessionfinish(SimpleNamespace(), 0)

    assert json.loads(path.read_text()) == {
        "tests/test_foo.py::test_a": 1.0,
        "tests/test_foo.py::test_b": 2.0,
    }


def test_duration_recorder_overwrites_updated_test(tmp_path):
    path = tmp_path / ".test_durations"
    path.write_text(json.dumps({"tests/test_foo.py::test_a": 5.0}))

    plugin = pytest_shard._DurationRecorderPlugin(path)
    plugin.pytest_runtest_logreport(
        MockReport(when="call", nodeid="tests/test_foo.py::test_a", duration=1.0)
    )
    plugin.pytest_sessionfinish(SimpleNamespace(), 0)

    assert json.loads(path.read_text())["tests/test_foo.py::test_a"] == 1.0


# ---------------------------------------------------------------------------
# pytest_collection_modifyitems
# ---------------------------------------------------------------------------


def test_pytest_collection_modifyitems_rejects_invalid_shard_id():
    config = SimpleNamespace(
        getoption=lambda name: {"shard_id": 2, "num_shards": 2, "shard_mode": "roundrobin"}[name]
    )
    with pytest.raises(ValueError, match=r"shard_id=2 must be less than num_shards=2"):
        pytest_shard.pytest_collection_modifyitems(config, [])


def test_pytest_collection_modifyitems_duration_file_not_found(tmp_path):
    missing = str(tmp_path / ".test_durations")
    config = SimpleNamespace(
        getoption=lambda name: {
            "shard_id": 0,
            "num_shards": 2,
            "shard_mode": "duration",
            "durations_path": missing,
        }[name]
    )
    with pytest.raises(FileNotFoundError, match="not found"):
        pytest_shard.pytest_collection_modifyitems(config, [MockItem("test_a")])


# ---------------------------------------------------------------------------
# pytest_report_collectionfinish
# ---------------------------------------------------------------------------


def test_pytest_report_collectionfinish_with_verbose_output():
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=1),
        getoption=lambda name: {"num_shards": 2, "shard_mode": "roundrobin"}[name],
    )
    items = [MockItem("test_module.py::test_first"), MockItem("test_module.py::test_second")]

    message = pytest_shard.pytest_report_collectionfinish(config, items)

    assert message == (
        "Running 2 items in this shard (mode: roundrobin): "
        "test_module.py::test_first, test_module.py::test_second"
    )
