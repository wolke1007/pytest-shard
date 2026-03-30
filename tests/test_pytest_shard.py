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

class MockItem(collections.namedtuple("_MockItemBase", "nodeid")):
    """Minimal pytest.Item stand-in with no xdist_group marker."""

    def get_closest_marker(self, name: str):
        return None


MockMarker = collections.namedtuple("MockMarker", ["args", "kwargs"])
MockReport = collections.namedtuple("MockReport", ["when", "nodeid", "duration"])


class MockItemWithMarker(MockItem):
    """MockItem that returns a single marker for 'xdist_group'."""

    _marker: MockMarker

    def __new__(cls, nodeid: str, marker: MockMarker):
        obj = super().__new__(cls, nodeid)
        obj._marker = marker  # type: ignore[misc]
        return obj

    def get_closest_marker(self, name: str):
        if name == "xdist_group":
            return self._marker
        return None


class MockTerminalReporter:
    """Collect write_line calls for assertions."""

    def __init__(self):
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


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


def test_load_durations_invalid_json(tmp_path):
    path = tmp_path / ".test_durations"
    path.write_text("not json {{{")
    with pytest.raises(ValueError, match="not valid JSON"):
        pytest_shard.load_durations(path)


def test_load_durations_not_a_dict(tmp_path):
    path = tmp_path / ".test_durations"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError, match="must contain a JSON object"):
        pytest_shard.load_durations(path)


def test_load_durations_non_numeric_values(tmp_path):
    path = tmp_path / ".test_durations"
    path.write_text(json.dumps({"tests/test_foo.py::test_a": "fast"}))
    with pytest.raises(ValueError, match="non-numeric duration values"):
        pytest_shard.load_durations(path)


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


def test_duration_recorder_rejects_invalid_existing_json(tmp_path):
    path = tmp_path / ".test_durations"
    path.write_text("not json {{{")

    plugin = pytest_shard._DurationRecorderPlugin(path)
    plugin.pytest_runtest_logreport(
        MockReport(when="call", nodeid="tests/test_foo.py::test_a", duration=1.0)
    )

    with pytest.raises(ValueError, match="not valid JSON"):
        plugin.pytest_sessionfinish(SimpleNamespace(), 0)


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
    opts = {"num_shards": 2, "shard_mode": "roundrobin", "list_shard_tests": False}
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=1),
        getoption=lambda name, default=None: opts.get(name, default),
    )
    items = [MockItem("test_module.py::test_first"), MockItem("test_module.py::test_second")]

    message = pytest_shard.pytest_report_collectionfinish(config, items)

    assert message == (
        "Running 2 items in this shard (mode: roundrobin): "
        "test_module.py::test_first, test_module.py::test_second"
    )


def test_pytest_report_collectionfinish_list_shard_tests_prints_one_per_line():
    opts = {"num_shards": 2, "shard_mode": "hash", "list_shard_tests": True}
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0),
        getoption=lambda name, default=None: opts.get(name, default),
    )
    items = [MockItem("test_module.py::test_first"), MockItem("test_module.py::test_second")]

    message = pytest_shard.pytest_report_collectionfinish(config, items)

    assert message == (
        "Running 2 items in this shard (mode: hash)\n"
        "  test_module.py::test_first\n"
        "  test_module.py::test_second"
    )


def test_pytest_report_collectionfinish_list_shard_tests_takes_priority_over_verbose():
    """--list-shard-tests produces one-per-line output even when -v is also set."""
    opts = {"num_shards": 2, "shard_mode": "hash", "list_shard_tests": True}
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=1),
        getoption=lambda name, default=None: opts.get(name, default),
    )
    items = [MockItem("test_module.py::test_a")]

    message = pytest_shard.pytest_report_collectionfinish(config, items)

    assert "\n  test_module.py::test_a" in message


def test_pytest_xdist_node_collection_finished_writes_list_once():
    opts = {"num_shards": 2, "shard_mode": "hash", "list_shard_tests": True}
    terminalreporter = MockTerminalReporter()
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0),
        getoption=lambda name, default=None: opts.get(name, default),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: terminalreporter if name == "terminalreporter" else None),
    )
    node = SimpleNamespace(config=config)
    ids = ["test_module.py::test_first", "test_module.py::test_second"]

    pytest_shard.pytest_xdist_node_collection_finished(node, ids)
    pytest_shard.pytest_xdist_node_collection_finished(node, ids)

    assert terminalreporter.lines == [
        "Running 2 items in this shard (mode: hash)\n"
        "  test_module.py::test_first\n"
        "  test_module.py::test_second"
    ]


def test_pytest_xdist_node_collection_finished_without_terminalreporter_is_noop():
    opts = {"num_shards": 2, "shard_mode": "hash", "list_shard_tests": True}
    config = SimpleNamespace(
        option=SimpleNamespace(verbose=0),
        getoption=lambda name, default=None: opts.get(name, default),
        pluginmanager=SimpleNamespace(get_plugin=lambda name: None),
    )
    node = SimpleNamespace(config=config)

    pytest_shard.pytest_xdist_node_collection_finished(node, ["test_module.py::test_first"])

    assert not hasattr(config, "_pytest_shard_xdist_report_emitted")


def test_list_shard_tests_hash_balanced_demo_suite_covers_100_without_overlap():
    """Two hash-balanced shards should print a complete, non-overlapping test list."""
    demo_suite = {
        "demo/demo_tests/test_arithmetic.py": {
            "TestAddition": [
                "test_add_positive", "test_add_zero", "test_add_negative", "test_add_floats", "test_add_large",
            ],
            "TestSubtraction": [
                "test_sub_positive", "test_sub_zero", "test_sub_negative", "test_sub_self", "test_sub_floats",
            ],
            "TestMultiplication": [
                "test_mul_positive", "test_mul_zero", "test_mul_one", "test_mul_negative", "test_mul_floats",
            ],
            "TestDivision": [
                "test_div_even", "test_div_float_result", "test_floordiv", "test_modulo", "test_div_by_zero_raises",
            ],
        },
        "demo/demo_tests/test_collections.py": {
            "TestList": ["test_append", "test_pop", "test_sort", "test_reverse", "test_slice"],
            "TestDict": ["test_get", "test_get_default", "test_keys", "test_update", "test_pop"],
            "TestSet": ["test_union", "test_intersection", "test_difference", "test_in", "test_add"],
            "TestTuple": ["test_index", "test_length", "test_unpack", "test_count", "test_immutable"],
        },
        "demo/demo_tests/test_comparisons.py": {
            "TestNumericComparisons": [
                "test_equal", "test_not_equal", "test_less", "test_greater", "test_less_equal",
            ],
            "TestBooleanLogic": ["test_and_true", "test_and_false", "test_or_true", "test_or_false", "test_not"],
            "TestIdentityMembership": [
                "test_is_none", "test_is_not_none", "test_in_list", "test_not_in_list", "test_in_string",
            ],
            "TestEdgeCases": [
                "test_none_equality", "test_bool_is_int", "test_empty_is_falsy", "test_zero_is_falsy",
                "test_chained_comparison",
            ],
        },
        "demo/demo_tests/test_strings.py": {
            "TestStringBasics": ["test_length", "test_empty", "test_concatenation", "test_repetition", "test_in_operator"],
            "TestStringCase": ["test_upper", "test_lower", "test_title", "test_swapcase", "test_capitalize"],
            "TestStringSearch": ["test_find", "test_find_missing", "test_replace", "test_startswith", "test_endswith"],
            "TestStringSplitJoin": ["test_split", "test_split_default", "test_join", "test_strip", "test_splitlines"],
        },
        "demo/demo_tests/test_types.py": {
            "TestTypeChecking": [
                "test_isinstance_int", "test_isinstance_str", "test_isinstance_list", "test_isinstance_multi",
                "test_type_equality",
            ],
            "TestTypeConversion": [
                "test_int_to_str", "test_str_to_int", "test_float_to_int", "test_int_to_float", "test_list_to_set",
            ],
            "TestBuiltins": ["test_abs", "test_round", "test_min", "test_max", "test_sum"],
            "TestIterables": ["test_enumerate", "test_zip", "test_map", "test_filter", "test_sorted"],
        },
    }
    items = [
        MockItem(f"{path}::{class_name}::{test_name}")
        for path, classes in demo_suite.items()
        for class_name, test_names in classes.items()
        for test_name in test_names
    ]
    assert len(items) == 100

    shard_nodeids: list[set[str]] = []
    for shard_id in range(2):
        assigned = pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=shard_id, num_shards=2)
        opts = {"num_shards": 2, "shard_mode": "hash-balanced", "list_shard_tests": True}
        config = SimpleNamespace(
            option=SimpleNamespace(verbose=0),
            getoption=lambda name, default=None: opts.get(name, default),
        )

        message = pytest_shard.pytest_report_collectionfinish(config, assigned)
        lines = message.splitlines()
        listed_nodeids = [line.strip() for line in lines[1:]]

        assert lines[0] == f"Running {len(assigned)} items in this shard (mode: hash-balanced)"
        assert len(listed_nodeids) == len(assigned)
        assert len(set(listed_nodeids)) == len(listed_nodeids)
        assert set(listed_nodeids) == {item.nodeid for item in assigned}
        shard_nodeids.append(set(listed_nodeids))

    assert len(shard_nodeids[0]) + len(shard_nodeids[1]) == 100
    assert shard_nodeids[0].isdisjoint(shard_nodeids[1])
    assert shard_nodeids[0] | shard_nodeids[1] == {item.nodeid for item in items}


# ---------------------------------------------------------------------------
# _hash_key_for_item
# ---------------------------------------------------------------------------


def test_hash_key_no_marker_returns_nodeid():
    item = MockItem("tests/test_foo.py::test_a")
    assert pytest_shard._hash_key_for_item(item) == "tests/test_foo.py::test_a"


def test_hash_key_xdist_group_positional():
    marker = MockMarker(args=("my_group",), kwargs={})
    item = MockItemWithMarker("tests/test_foo.py::test_a", marker)
    assert pytest_shard._hash_key_for_item(item) == "xdist_group:my_group"


def test_hash_key_xdist_group_keyword():
    marker = MockMarker(args=(), kwargs={"name": "my_group"})
    item = MockItemWithMarker("tests/test_foo.py::test_a", marker)
    assert pytest_shard._hash_key_for_item(item) == "xdist_group:my_group"


def test_hash_key_xdist_group_empty_name_falls_back_to_nodeid():
    """xdist_group with no usable name should fall back to node ID."""
    marker = MockMarker(args=(), kwargs={})
    item = MockItemWithMarker("tests/test_foo.py::test_a", marker)
    assert pytest_shard._hash_key_for_item(item) == "tests/test_foo.py::test_a"


# ---------------------------------------------------------------------------
# Hash mode: xdist_group grouping guarantees
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_same_xdist_group_lands_on_same_shard():
    """All items sharing an xdist_group must end up on exactly one shard."""
    marker = MockMarker(args=("group_a",), kwargs={})
    items = [MockItemWithMarker(f"tests/test_{i}.py::test_x", marker) for i in range(6)]
    num_shards = 4

    shard_results = [
        pytest_shard.filter_items_by_shard(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    non_empty = [r for r in shard_results if r]
    assert len(non_empty) == 1, "all items in the same xdist_group must land on one shard"
    assert set(non_empty[0]) == set(items)


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_parametrize_with_xdist_group_same_shard():
    """Parametrized variants sharing an xdist_group must all land on the same shard."""
    marker = MockMarker(args=("param_group",), kwargs={})
    items = [
        MockItemWithMarker(f"tests/test_foo.py::test_a[{p}]", marker)
        for p in ["x", "y", "z", "w"]
    ]
    num_shards = 3

    shard_results = [
        pytest_shard.filter_items_by_shard(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    non_empty = [r for r in shard_results if r]
    assert len(non_empty) == 1
    assert set(non_empty[0]) == set(items)


def test_no_xdist_group_hash_behavior_unchanged():
    """Items without xdist_group must be distributed as before (regression guard)."""
    items = [MockItem(f"test_{i}") for i in range(20)]
    num_shards = 4
    filtered = [
        pytest_shard.filter_items_by_shard(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    all_filtered = list(itertools.chain(*filtered))
    assert len(all_filtered) == len(items)
    assert set(all_filtered) == set(items)


# ---------------------------------------------------------------------------
# _warn_if_group_dominates_shard
# ---------------------------------------------------------------------------


def test_group_size_warning_emitted_when_over_threshold():
    marker = MockMarker(args=("big_group",), kwargs={})
    # 4 items in big_group + 1 item without = big_group is 80 % → over 50 % threshold
    items = [MockItemWithMarker(f"tests/test_{i}.py::test_x", marker) for i in range(4)]
    items.append(MockItem("tests/test_other.py::test_y"))

    with pytest.warns(UserWarning, match="big_group"):
        pytest_shard._warn_if_group_dominates_shard(items)


def test_group_size_warning_not_emitted_when_under_threshold():
    marker = MockMarker(args=("small_group",), kwargs={})
    # 2 items in small_group + 5 others = 28 % → under threshold
    group_items = [MockItemWithMarker(f"tests/test_{i}.py::test_x", marker) for i in range(2)]
    other_items = [MockItem(f"tests/test_other_{i}.py::test_y") for i in range(5)]
    items = group_items + other_items

    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("error")
        pytest_shard._warn_if_group_dominates_shard(items)  # must not raise


# ---------------------------------------------------------------------------
# Hash-balanced mode: filter_items_by_shard_group_balanced
# ---------------------------------------------------------------------------


def _make_group_items(group_name: str, count: int) -> list[MockItemWithMarker]:
    marker = MockMarker(args=(group_name,), kwargs={})
    return [MockItemWithMarker(f"tests/test_{group_name}_{i}.py::test_x", marker) for i in range(count)]


def test_hash_balanced_covers_all_items():
    """Every item must appear in exactly one shard (no overlap, no gap)."""
    items = (
        _make_group_items("database", 5)
        + _make_group_items("auth", 4)
        + _make_group_items("cache", 4)
        + [MockItem(f"tests/test_standalone_{i}.py::test_x") for i in range(4)]
    )
    num_shards = 3
    all_results = list(itertools.chain.from_iterable(
        pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ))
    assert len(all_results) == len(items)
    assert set(all_results) == set(items)


def test_hash_balanced_groups_stay_together():
    """All tests in the same xdist_group must land on the same shard."""
    items = (
        _make_group_items("database", 5)
        + _make_group_items("auth", 4)
        + _make_group_items("cache", 4)
    )
    num_shards = 3

    for group_name in ("database", "auth", "cache"):
        marker = MockMarker(args=(group_name,), kwargs={})
        group_items = set(
            item for item in items
            if isinstance(item, MockItemWithMarker) and item._marker == marker
        )
        shards_containing_group = [
            i for i in range(num_shards)
            if set(pytest_shard.filter_items_by_shard_group_balanced(
                items, shard_id=i, num_shards=num_shards
            )) & group_items
        ]
        assert len(shards_containing_group) == 1, (
            f"group '{group_name}' must land on exactly one shard, got {shards_containing_group}"
        )


def test_hash_balanced_is_deterministic():
    """Same input must always produce the same assignment across multiple calls."""
    items = (
        _make_group_items("database", 5)
        + _make_group_items("auth", 4)
        + _make_group_items("cache", 4)
        + [MockItem(f"tests/test_standalone_{i}.py::test_x") for i in range(4)]
    )
    num_shards = 3

    first_run = [
        pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    second_run = [
        pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ]
    assert first_run == second_run, "hash-balanced must be deterministic for the same input"


def test_hash_balanced_large_groups_on_different_shards():
    """Two groups larger than 1/num_shards of total tests must not share a shard."""
    # database(5) and auth(4) would both hash to shard 0 in plain hash mode.
    # hash-balanced must separate them.
    items = (
        _make_group_items("database", 5)
        + _make_group_items("auth", 4)
        + _make_group_items("cache", 4)
    )
    num_shards = 3

    shard_results = [
        set(pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=i, num_shards=num_shards))
        for i in range(num_shards)
    ]

    database_items = set(_make_group_items("database", 5))
    auth_items = set(_make_group_items("auth", 4))

    database_shard = next(i for i, s in enumerate(shard_results) if database_items <= s)
    auth_shard = next(i for i, s in enumerate(shard_results) if auth_items <= s)

    assert database_shard != auth_shard, (
        "database and auth are the two largest groups and must land on different shards"
    )


def test_hash_balanced_no_groups_behaves_like_hash():
    """With no xdist_group markers, hash-balanced must produce the same result as hash."""
    items = [MockItem(f"test_{i}") for i in range(20)]
    num_shards = 4

    balanced_results = [
        set(pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=i, num_shards=num_shards))
        for i in range(num_shards)
    ]
    hash_results = [
        set(pytest_shard.filter_items_by_shard(items, shard_id=i, num_shards=num_shards))
        for i in range(num_shards)
    ]
    assert balanced_results == hash_results


def test_hash_balanced_all_grouped_covers_all():
    """When every item has an xdist_group, all items must still be covered."""
    items = _make_group_items("alpha", 3) + _make_group_items("beta", 3) + _make_group_items("gamma", 3)
    num_shards = 3
    all_results = list(itertools.chain.from_iterable(
        pytest_shard.filter_items_by_shard_group_balanced(items, shard_id=i, num_shards=num_shards)
        for i in range(num_shards)
    ))
    assert set(all_results) == set(items)
    assert len(all_results) == len(items)
