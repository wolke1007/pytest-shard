"""Shard tests to support parallelism across multiple machines."""

import hashlib
import json
import pathlib
import warnings
from collections.abc import Iterable, Sequence

import pytest


def non_negative_int(x: int | str) -> int:
    x = int(x)
    if x < 0:
        raise ValueError(f"Argument {x} must be non-negative")
    return x


def positive_int(x: int | str) -> int:
    x = int(x)
    if x <= 0:
        raise ValueError(f"Argument {x} must be positive")
    return x


# ---------------------------------------------------------------------------
# Duration recorder plugin
# ---------------------------------------------------------------------------


class _DurationRecorderPlugin:
    """Collects per-test durations and writes them to a JSON file at session end.

    Existing entries in the file are preserved; only tests that ran in this
    session are overwritten. This makes it safe to run on a sharded subset —
    merge multiple shard files afterward to obtain the full picture.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self._path = path
        self._durations: dict[str, float] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call":
            self._durations[report.nodeid] = report.duration

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        existing: dict[str, float] = {}
        if self._path.exists():
            existing = load_durations(self._path)
        existing.update(self._durations)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(existing, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# pytest option registration
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add pytest-shard specific configuration parameters."""
    group = parser.getgroup("shard")
    group.addoption(
        "--shard-id",
        dest="shard_id",
        type=non_negative_int,
        default=0,
        help="Number of this shard.",
    )
    group.addoption(
        "--num-shards",
        dest="num_shards",
        type=positive_int,
        default=1,
        help="Total number of shards.",
    )
    group.addoption(
        "--shard-mode",
        dest="shard_mode",
        choices=["roundrobin", "hash", "hash-balanced", "duration"],
        default="roundrobin",
        help=(
            "Algorithm used to assign tests to shards. "
            "'roundrobin' (default): sort by node ID then interleave, guarantees count balance. "
            "'hash': SHA-256 hash of node ID, stateless and per-test stable. "
            "'hash-balanced': LPT bin-packing for xdist_group groups to avoid collision, "
            "hash by node ID for ungrouped tests; deterministic for the same test collection. "
            "'duration': greedy bin-packing by duration, requires --durations-path."
        ),
    )
    group.addoption(
        "--durations-path",
        dest="durations_path",
        default=".test_durations",
        help=(
            "Path to the JSON file used for reading or writing test durations. "
            "Used when --shard-mode=duration (read) or --store-durations (write). "
            "Compatible with the .test_durations format produced by pytest-split. "
            "Defaults to .test_durations in the current directory."
        ),
    )
    group.addoption(
        "--store-durations",
        dest="store_durations",
        action="store_true",
        default=False,
        help=(
            "Record each test's duration and write them to --durations-path at session end. "
            "Existing entries are preserved; only tests that ran this session are overwritten. "
            "When running sharded, each shard should write to its own path; merge the files "
            "afterward to obtain complete duration data."
        ),
    )
    group.addoption(
        "--list-shard-tests",
        dest="list_shard_tests",
        action="store_true",
        default=False,
        help=(
            "Print every test node ID assigned to this shard before the run starts, "
            "one per line. Useful for verifying shard assignment without relying on -v."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the duration recorder plugin when --store-durations is active."""
    if config.getoption("store_durations", default=False):
        path = pathlib.Path(config.getoption("durations_path", default=".test_durations"))
        config.pluginmanager.register(
            _DurationRecorderPlugin(path),
            "_pytest_shard_duration_recorder",
        )


# ---------------------------------------------------------------------------
# Collection finish reporting
# ---------------------------------------------------------------------------


def _format_collection_report(config: pytest.Config, nodeids: Sequence[str]) -> str:
    """Format the per-shard collection report for both local and xdist runs."""
    mode = config.getoption("shard_mode")
    msg = f"Running {len(nodeids)} items in this shard (mode: {mode})"
    if config.getoption("list_shard_tests", default=False):
        lines = [msg] + [f"  {nodeid}" for nodeid in nodeids]
        return "\n".join(lines)
    if config.option.verbose > 0 and config.getoption("num_shards") > 1:
        msg += ": " + ", ".join(nodeids)
    return msg


def pytest_report_collectionfinish(config: pytest.Config, items: Sequence[pytest.Item]) -> str:
    """Log how many and, if requested, which items are tested in this shard."""
    return _format_collection_report(config, [item.nodeid for item in items])


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: object, ids: Sequence[str]) -> None:
    """Emit the shard report from the xdist controller so CI logs see it reliably."""
    config = node.config
    if getattr(config, "_pytest_shard_xdist_report_emitted", False):
        return

    terminalreporter = config.pluginmanager.get_plugin("terminalreporter")
    if terminalreporter is None:
        return

    terminalreporter.write_line(_format_collection_report(config, ids))
    setattr(config, "_pytest_shard_xdist_report_emitted", True)


# ---------------------------------------------------------------------------
# Sharding algorithms
# ---------------------------------------------------------------------------


def sha256hash(x: str) -> int:
    return int.from_bytes(hashlib.sha256(x.encode()).digest(), "little")


def _hash_key_for_item(item: pytest.Item) -> str:
    """Return the hash key for an item: xdist_group name if present, else node ID.

    In hash mode, tests sharing an xdist_group are guaranteed to land on the
    same shard. This respects the grouping intent already declared via
    pytest-xdist markers.

    Supports both argument forms:
      @pytest.mark.xdist_group("name")        — positional
      @pytest.mark.xdist_group(name="name")   — keyword
    """
    marker = item.get_closest_marker("xdist_group")
    if marker:
        group_name = (
            marker.args[0]
            if marker.args
            else marker.kwargs.get("name", "")
        )
        if group_name:
            return f"xdist_group:{group_name}"
    return item.nodeid


def filter_items_by_shard(
    items: Iterable[pytest.Item], shard_id: int, num_shards: int
) -> Sequence[pytest.Item]:
    """Hash mode: assign each test via SHA-256(hash_key) % num_shards.

    The hash key is the xdist_group name (if the test carries that marker) or
    the node ID. Tests sharing an xdist_group are therefore guaranteed to land
    on the same shard.

    Stateless and per-test stable — adding or removing other tests never
    changes which shard an existing test belongs to. Distribution is
    statistically uniform but may be uneven for small test counts or large
    xdist_groups.
    """
    result = [
        item
        for item in items
        if sha256hash(_hash_key_for_item(item)) % num_shards == shard_id
    ]
    _warn_if_group_dominates_shard(result)
    return result


def _warn_if_group_dominates_shard(
    items: Sequence[pytest.Item], threshold: float = 0.5
) -> None:
    """Warn when a single xdist_group accounts for more than threshold of shard items."""
    if len(items) < 2:
        return
    group_counts: dict[str, int] = {}
    for item in items:
        key = _hash_key_for_item(item)
        if key.startswith("xdist_group:"):
            group_name = key[len("xdist_group:"):]
            group_counts[group_name] = group_counts.get(group_name, 0) + 1
    total = len(items)
    for group_name, count in group_counts.items():
        if count / total > threshold:
            warnings.warn(
                f"xdist_group {group_name!r} accounts for {count}/{total} tests "
                f"({count / total:.0%}) in this shard, which may cause uneven shard sizes.",
                stacklevel=3,
            )


def filter_items_by_shard_group_balanced(
    items: Iterable[pytest.Item], shard_id: int, num_shards: int
) -> Sequence[pytest.Item]:
    """Hash-balanced mode: LPT bin-packing for xdist_group groups, hash for the rest.

    Groups (tests sharing an xdist_group marker) are treated as atomic units and
    assigned to shards using the Longest Processing Time (LPT) greedy algorithm:
    sort groups by size descending (name ascending as tiebreaker), then assign
    each group to the shard with the fewest tests so far. This prevents multiple
    large groups from colliding on the same shard.

    Ungrouped tests (no xdist_group marker) are assigned via SHA-256(node_id) %
    num_shards, identical to plain hash mode.

    Deterministic: given the same test collection, every shard process computes the
    same global assignment table independently and filters to its own subset — no
    inter-process coordination is needed, and there is no risk of overlap or gaps.
    """
    items_list = list(items)

    grouped: dict[str, list[pytest.Item]] = {}
    ungrouped: list[pytest.Item] = []
    for item in items_list:
        key = _hash_key_for_item(item)
        if key.startswith("xdist_group:"):
            group_name = key[len("xdist_group:"):]
            grouped.setdefault(group_name, []).append(item)
        else:
            ungrouped.append(item)

    # Sort by size desc, name asc as tiebreaker — fully deterministic
    sorted_groups = sorted(grouped.items(), key=lambda x: (-len(x[1]), x[0]))

    shard_counts = [0] * num_shards
    shard_items: list[list[pytest.Item]] = [[] for _ in range(num_shards)]

    for _group_name, group_items in sorted_groups:
        target = min(range(num_shards), key=lambda i: shard_counts[i])
        shard_items[target].extend(group_items)
        shard_counts[target] += len(group_items)

    for item in ungrouped:
        target = sha256hash(item.nodeid) % num_shards
        shard_items[target].append(item)

    return shard_items[shard_id]


def filter_items_round_robin(
    items: Iterable[pytest.Item], shard_id: int, num_shards: int
) -> Sequence[pytest.Item]:
    """Round-robin mode: sort by node ID then assign by index % num_shards.

    Guarantees that shard sizes differ by at most 1 regardless of test count.
    Trade-off: adding or removing a test can shift the assignments of other
    tests because the sort order changes.
    """
    sorted_items = sorted(items, key=lambda item: item.nodeid)
    return [item for i, item in enumerate(sorted_items) if i % num_shards == shard_id]


def load_durations(path: str | pathlib.Path) -> dict[str, float]:
    """Load a node-ID → duration (seconds) mapping from a JSON file."""
    path = pathlib.Path(path)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"--durations-path={str(path)!r} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"--durations-path={str(path)!r} must contain a JSON object "
            f"(got {type(data).__name__})"
        )
    bad = {k: v for k, v in data.items() if not isinstance(v, (int, float))}
    if bad:
        examples = ", ".join(f"{k!r}: {v!r}" for k, v in list(bad.items())[:3])
        raise ValueError(
            f"--durations-path={str(path)!r} has non-numeric duration values: {examples}"
        )
    return data


def filter_items_by_duration(
    items: Iterable[pytest.Item],
    shard_id: int,
    num_shards: int,
    durations: dict[str, float],
    default_duration: float = 1.0,
) -> Sequence[pytest.Item]:
    """Duration mode: greedy bin-packing (LPT) to minimise the longest shard.

    Tests without a recorded duration are assigned `default_duration`.
    Uses the Longest Processing Time (LPT) greedy algorithm: sort tests
    by duration descending, then assign each to the shard with the
    smallest accumulated time so far.
    """
    sorted_items = sorted(
        items,
        key=lambda item: durations.get(item.nodeid, default_duration),
        reverse=True,
    )

    shard_times: list[float] = [0.0] * num_shards
    shard_items: list[list[pytest.Item]] = [[] for _ in range(num_shards)]

    for item in sorted_items:
        duration = durations.get(item.nodeid, default_duration)
        min_shard = min(range(num_shards), key=lambda i: shard_times[i])
        shard_items[min_shard].append(item)
        shard_times[min_shard] += duration

    return shard_items[shard_id]


# ---------------------------------------------------------------------------
# pytest hook
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mutate the collection to consist of just items to be tested in this shard."""
    shard_id = config.getoption("shard_id")
    num_shards = config.getoption("num_shards")
    shard_mode = config.getoption("shard_mode")

    if shard_id >= num_shards:
        raise ValueError(f"shard_id={shard_id} must be less than num_shards={num_shards}")

    if shard_mode == "roundrobin":
        items[:] = filter_items_round_robin(items, shard_id, num_shards)
    elif shard_mode == "hash":
        items[:] = filter_items_by_shard(items, shard_id, num_shards)
    elif shard_mode == "hash-balanced":
        items[:] = filter_items_by_shard_group_balanced(items, shard_id, num_shards)
    elif shard_mode == "duration":
        durations_path = pathlib.Path(config.getoption("durations_path"))
        if not durations_path.exists():
            raise FileNotFoundError(
                f"--shard-mode=duration requires a durations file. "
                f"{str(durations_path)!r} not found. "
                f"Run with --store-durations first to generate it."
            )
        durations = load_durations(durations_path)
        items[:] = filter_items_by_duration(items, shard_id, num_shards, durations)
