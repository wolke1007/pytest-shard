[繁體中文](allure-integration.zh-TW.md) | **English**

# Allure Report Integration with pytest-shard

This guide explains how to collect [Allure](https://allurereport.org/) test results across multiple shards, merge them into a single report, and verify the output — including the Timeline view for parallel execution analysis.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [How It Works](#how-it-works)
3. [Step-by-Step Setup](#step-by-step-setup)
   - [Install dependencies](#1-install-dependencies)
   - [Run each shard with Allure output](#2-run-each-shard-with-allure-output)
   - [Merge results and generate the report](#3-merge-results-and-generate-the-report)
4. [Parallel Execution Example](#parallel-execution-example)
   - [Demo: 30 tests across 3 shards](#demo-30-tests-across-3-shards)
   - [Automating with nox](#automating-with-nox)
   - [Timeline result](#timeline-result)
5. [Duration-Based Load Balancing](#duration-based-load-balancing)
   - [The problem: uneven runtimes under round-robin](#the-problem-uneven-runtimes-under-round-robin)
   - [The solution: record durations, then re-shard](#the-solution-record-durations-then-re-shard)
   - [Demo: two-run comparison](#demo-two-run-comparison)
   - [Timeline comparison](#timeline-comparison)
6. [CI/CD Integration](#cicd-integration)
   - [GitHub Actions](#github-actions)
   - [CircleCI](#circleci)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Requirement | Version | Install |
|-------------|---------|---------|
| pytest-shard | ≥ 0.1.2 | `pip install pytest-shard` |
| allure-pytest | ≥ 2.13 | `pip install allure-pytest` |
| Allure CLI | ≥ 2.20 | [allure CLI install guide](https://allurereport.org/docs/install/) |

---

## How It Works

```
Machine / Worker 0                Machine / Worker 1                Machine / Worker N
─────────────────────             ─────────────────────             ─────────────────────
pytest --shard-id=0               pytest --shard-id=1               pytest --shard-id=N
      --num-shards=N                    --num-shards=N                    --num-shards=N
      --alluredir=results/shard-0       --alluredir=results/shard-1       --alluredir=results/shard-N
           │                                  │                                  │
           └──────────────────────────────────┴──────────────────────────────────┘
                                              │
                                   Copy all *-result.json
                                   into results/combined/
                                              │
                                   allure generate results/combined
                                              │
                                     allure-report/  ← single merged report
```

**Why copy instead of pointing `allure generate` at multiple directories?**
`allure generate` accepts only one source directory. Because `allure-pytest` names every result file with a UUID (e.g. `3f2a1b…-result.json`), copying files from separate shard directories into one `combined/` directory is collision-free and straightforward.

---

## Step-by-Step Setup

### 1. Install dependencies

```bash
pip install pytest-shard allure-pytest
# Also install the Allure CLI: https://allurereport.org/docs/install/
```

### 2. Run each shard with Allure output

Each shard must write its results to a **separate directory** so the outputs do not interfere with each other.

```bash
# Shard 0 of 3
pytest --shard-id=0 --num-shards=3 --alluredir=allure-results/shard-0

# Shard 1 of 3
pytest --shard-id=1 --num-shards=3 --alluredir=allure-results/shard-1

# Shard 2 of 3
pytest --shard-id=2 --num-shards=3 --alluredir=allure-results/shard-2
```

> These three commands can run on separate machines or as parallel jobs in CI — see [CI/CD Integration](#cicd-integration).

### 3. Merge results and generate the report

```bash
# Copy all shard results into one directory
mkdir -p allure-results/combined
cp allure-results/shard-0/* allure-results/combined/
cp allure-results/shard-1/* allure-results/combined/
cp allure-results/shard-2/* allure-results/combined/

# Generate the unified report
allure generate allure-results/combined -o allure-report --clean

# Open the report in a browser
allure open allure-report
```

---

## Parallel Execution Example

### Demo: 30 tests across 3 shards

The following example runs **30 tests** (each simulating ~1 s of work) distributed across **3 shards** that execute in parallel on the local machine.

**Test layout:**

```
demo30_tests/
├── conftest.py          # adds 1 s to each test's call phase (not setup)
├── test_group_a.py      # 10 arithmetic tests
├── test_group_b.py      # 10 string tests
└── test_group_c.py      # 10 collection tests
```

**`demo30_tests/conftest.py`** — the key detail is using `pytest_runtest_call` instead of a fixture so that the delay is recorded by Allure as test-body duration (not setup):

```python
import time

def pytest_runtest_call(item):
    time.sleep(1)
```

> **Why `pytest_runtest_call` and not a fixture?**
> Allure separates execution into three stages: *Set up*, *Test body*, and *Tear down*.
> A fixture with `time.sleep(1)` before `yield` runs in *Set up*, leaving the test-body duration at 0 s — the Timeline shows no visible blocks.
> `pytest_runtest_call` runs during *Test body*, so Allure records the full 1 s as test duration.

**Shard distribution (hash-based, 30 tests, 3 shards):**

| Shard | Tests assigned | Wall time |
|-------|---------------|-----------|
| 0 | 13 | ~13 s |
| 1 | 11 | ~11 s |
| 2 | 6  | ~6 s  |
| **Total (parallel)** | **30** | **~13 s** |
| Total (sequential)   | 30 | ~30 s |

The uneven distribution (6 vs 13) is expected: `pytest-shard` assigns tests via `SHA-256(test_node_id) % num_shards`, which is statistically uniform but not guaranteed to be perfectly balanced for small test counts.

### Automating with nox

The session below launches all 3 shard processes simultaneously using `subprocess.Popen`, waits for them to finish, then merges and generates the report:

```python
# noxfile.py
import pathlib
import shutil
import subprocess
import sys

import nox

ALLURE_RESULTS_DIR = pathlib.Path("allure-results")
ALLURE_REPORT_DIR  = pathlib.Path("allure-report")

@nox.session(name="demo-3-shards-parallel", python=False)
def demo_three_shards_parallel(session: nox.Session) -> None:
    num_shards  = 3
    demo_results = ALLURE_RESULTS_DIR / "demo30"
    demo_report  = ALLURE_REPORT_DIR.parent / "allure-report-demo30"

    shutil.rmtree(demo_results, ignore_errors=True)
    for shard_id in range(num_shards):
        (demo_results / f"shard-{shard_id}").mkdir(parents=True)

    # Launch all shards concurrently — each gets its own OS process
    log_files, procs = [], []
    for shard_id in range(num_shards):
        shard_dir = demo_results / f"shard-{shard_id}"
        log_file  = (demo_results / f"shard-{shard_id}.log").open("w")
        log_files.append(log_file)
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "pytest",
                f"--shard-id={shard_id}",
                f"--num-shards={num_shards}",
                f"--alluredir={shard_dir}",
                "-v", "demo30_tests",
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        procs.append(proc)
        session.log(f"Shard {shard_id} started (PID {proc.pid})")

    # Wait for all shards and print their logs
    for shard_id, (proc, log_file) in enumerate(zip(procs, log_files)):
        proc.wait()
        log_file.close()
        log_text = (demo_results / f"shard-{shard_id}.log").read_text()
        session.log(f"--- shard-{shard_id} (exit {proc.returncode}) ---\n{log_text}")
        if proc.returncode != 0:
            session.error(f"Shard {shard_id} failed")

    # Merge & generate report
    combined_dir = demo_results / "combined"
    combined_dir.mkdir()
    for shard_id in range(num_shards):
        for f in (demo_results / f"shard-{shard_id}").iterdir():
            shutil.copy2(f, combined_dir / f.name)

    shutil.rmtree(demo_report, ignore_errors=True)
    session.run(
        "allure", "generate", str(combined_dir),
        "-o", str(demo_report), "--clean", external=True,
    )
```

Run it with:

```bash
nox -s demo-3-shards-parallel
allure open allure-report-demo30
```

### Timeline result

After opening the report, navigate to **Timeline** in the left sidebar.

![Allure Timeline — 3 shards, 30 tests, each 1 s](image/local_test_with_3_shards_30_cases.png)

The Timeline shows each shard as a horizontal track. Because all three processes started at the same wall-clock time, the tracks begin at the same point on the x-axis. The longest track (shard-0, 13 tests) determines the total elapsed time — approximately **13 seconds** instead of the 30 seconds a sequential run would take.

---

## Duration-Based Load Balancing

### The problem: uneven runtimes under round-robin

Round-robin is the default sharding mode and guarantees that each shard receives at most one extra test compared to the others. This is fair by **count** — but not by **time**. When tests have very different runtimes, a shard that happens to receive all the slow tests will finish long after the rest, making that shard the bottleneck for the entire CI pipeline.

**Example:** 15 tests assigned to 3 shards by round-robin (sorted by node ID, then `index % 3`):

| Shard | Tests assigned | Individual durations | Total |
|-------|---------------|----------------------|-------|
| 0 | w01, w04, w07, w10, w13 | 10 + 9 + 8 + 7 + 6 s | **40 s** |
| 1 | w02, w05, w08, w11, w14 | 1 + 1 + 1 + 1 + 2 s  | 6 s   |
| 2 | w03, w06, w09, w12, w15 | 1 + 1 + 1 + 1 + 2 s  | 6 s   |

Shard 0 is **6.5× slower** than the others. The pipeline wall time is determined by the slowest shard — 40 s instead of an optimal ~18 s.

### The solution: record durations, then re-shard

`pytest-shard` provides two flags that work together:

| Flag | Purpose |
|------|---------|
| `--store-durations` | Record each test's actual call-phase duration and write it to a JSON file |
| `--durations-path=PATH` | Path for reading (or writing) the durations file (default: `.test_durations`) |
| `--shard-mode=duration` | Use the durations file to assign tests via LPT bin-packing |

**Workflow:**

```
Run 1 (round-robin + --store-durations)
  → produces .test_durations  {"test_w01": 10.0, "test_w04": 9.0, ...}

Run 2 (--shard-mode=duration --durations-path=.test_durations)
  → LPT bin-packing balances total time per shard
```

**LPT (Longest Processing Time)** assigns each test greedily to whichever shard currently has the least accumulated time. This is a well-known approximation for the makespan minimisation problem; in practice it produces near-optimal balance.

### Demo: two-run comparison

The `demo-duration-comparison` nox session runs the full two-pass workflow:

```bash
nox -s demo-duration-comparison
```

**What it does:**

1. **Run 1 — round-robin + `--store-durations`:** Launches 3 shards in parallel. Each shard writes its own `.test_durations` file (separate paths avoid concurrent write conflicts). After all shards finish, the per-shard files are merged into a single `allure-results/demo-duration/.test_durations`.

2. **Run 2 — duration mode:** Launches 3 shards in parallel again, this time passing `--shard-mode=duration --durations-path=<merged file>`. LPT bin-packing re-distributes the tests based on recorded timings.

3. Two separate Allure reports are generated — one for each run — so you can inspect the Timeline side by side.

**Abbreviated nox session:**

```python
# Run 1: each shard writes its own durations file
_run_shards_parallel(
    session,
    test_dir="demo_duration_tests",
    num_shards=3,
    results_root=first_results,
    extra_args=["--shard-mode=roundrobin", "--store-durations"],
    per_shard_args={
        i: [f"--durations-path={first_results / f'shard-{i}' / '.test_durations'}"]
        for i in range(3)
    },
)

# Merge per-shard duration files
merged = {}
for i in range(3):
    merged.update(json.loads((first_results / f"shard-{i}/.test_durations").read_text()))
durations_path.write_text(json.dumps(merged, indent=2, sort_keys=True))

# Run 2: use merged durations for balanced sharding
_run_shards_parallel(
    session,
    test_dir="demo_duration_tests",
    num_shards=3,
    results_root=second_results,
    extra_args=["--shard-mode=duration", f"--durations-path={durations_path}"],
)
```

**Observed results:**

| | Shard 0 | Shard 1 | Shard 2 | Wall time (bottleneck) |
|-|---------|---------|---------|------------------------|
| Run 1 (round-robin) | **40 s** | 6 s | 6 s | **40 s** |
| Run 2 (duration)    | 17 s    | 17 s | **18 s** | **18 s** |

Duration mode reduced the pipeline wall time from **40 s → 18 s**, a **55% improvement**, simply by re-ordering which tests each shard receives.

### Timeline comparison

Open both reports and navigate to **Timeline** in the left sidebar to see the difference visually.

**Run 1 — round-robin (unbalanced):**

![Allure Timeline — Run 1, round-robin, unbalanced](image/without_test_durations_file.png)

Shard 0's track extends far to the right while shards 1 and 2 finish in a fraction of the time. The pipeline must wait for shard 0 before it can proceed.

**Run 2 — duration mode (balanced):**

![Allure Timeline — Run 2, duration mode, balanced](image/wtih_test_durations_file.png)

All three tracks end at nearly the same point on the x-axis. No single shard is the bottleneck; the total wall time equals the theoretical minimum imposed by the single heaviest test.

---

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/test.yml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        shard_id: [0, 1, 2]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install dependencies
        run: pip install -e .[dev]

      - name: Run tests (shard ${{ matrix.shard_id }} of 3)
        run: |
          pytest \
            --shard-id=${{ matrix.shard_id }} \
            --num-shards=3 \
            --alluredir=allure-results/shard-${{ matrix.shard_id }}

      - name: Upload shard results
        uses: actions/upload-artifact@v4
        with:
          name: allure-results-shard-${{ matrix.shard_id }}
          path: allure-results/shard-${{ matrix.shard_id }}/

  allure-report:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          pattern: allure-results-shard-*
          merge-multiple: true
          path: allure-results/combined/

      - name: Generate Allure report
        uses: simple-elf/allure-report-action@v1
        with:
          allure_results: allure-results/combined
          allure_report: allure-report

      - name: Publish report to GitHub Pages
        uses: peaceiris/actions-gh-pages@v3
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: allure-report
```

### CircleCI

```yaml
# .circleci/config.yml
version: 2.1
jobs:
  test:
    parallelism: 3
    docker:
      - image: cimg/python:3.13
    steps:
      - checkout
      - run: pip install -e .[dev]
      - run:
          name: Run shard
          command: |
            pytest \
              --shard-id=${CIRCLE_NODE_INDEX} \
              --num-shards=${CIRCLE_NODE_TOTAL} \
              --alluredir=allure-results/shard-${CIRCLE_NODE_INDEX}
      - persist_to_workspace:
          root: .
          paths: [allure-results]

  allure-report:
    docker:
      - image: cimg/python:3.13
    steps:
      - attach_workspace:
          at: .
      - run:
          name: Merge and generate report
          command: |
            mkdir -p allure-results/combined
            cp allure-results/shard-*/* allure-results/combined/
            allure generate allure-results/combined -o allure-report --clean
      - store_artifacts:
          path: allure-report

workflows:
  main:
    jobs:
      - test
      - allure-report:
          requires: [test]
```

---

## Troubleshooting

**Timeline shows 0 s duration for all tests**

The most common cause is placing `time.sleep()` (or any slow setup code) inside a pytest fixture. Allure records fixture execution as *Set up*, not *Test body*.

| Approach | Allure stage | Shows in Timeline |
|----------|-------------|-------------------|
| `@pytest.fixture` with sleep before `yield` | Set up | No |
| `@pytest.fixture` with sleep after `yield` | Tear down | No |
| `pytest_runtest_call` hook with sleep | Test body | **Yes** |

**Shard results are missing from the merged report**

Verify that the `--alluredir` paths for each shard are distinct. If two shards write to the same directory concurrently, result files may be overwritten (though UUID naming makes this unlikely, environment-level race conditions can still occur).

**`allure generate` warns about duplicate test names**

This should not happen with `pytest-shard` since each test node ID is unique and assigned to exactly one shard. If you see duplicates, check that the same shard is not being run twice with different `--alluredir` values that are later both copied into `combined/`.
