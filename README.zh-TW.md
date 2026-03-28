[![PyPI version](https://badge.fury.io/py/pytest-shard-cloudc.svg)](https://badge.fury.io/py/pytest-shard-cloudc)

**繁體中文** | [English](README.md)

# pytest-shard

> **本專案為 [Cloud Chen](https://github.com/wolke1007) fork 自 [AdamGleave/pytest-shard](https://github.com/AdamGleave/pytest-shard) 的修改版本。**
> 主要改動包含：Allure report 整合、多 shard 結果合併、nox 工具鏈，以及以 `pyproject.toml` 為核心的現代化套件設定。

`pytest-shard` 會以個別測試案例為粒度，將測試套件分散到多台機器或 CI worker 上執行。預設會先依 node ID 排序，再以 round-robin 方式分配到各 shard，因此即使所有測試都在同一個檔案或同一個參數化方法中，也能實現平行化。

## 運作示意

更容易理解的分開圖解，請直接參考 [分派模式指南](doc/sharding-modes.zh-TW.md) 中 `roundrobin`、`hash`、`duration` 各自的示意圖。

## 能做什麼

| 功能 | 說明 |
|------|------|
| **Round-robin 分配**（預設） | 依 node ID 排序後輪流分配，保證各 shard 的測試數量差距不超過 1 |
| **Hash-based 分配** | 透過 `SHA-256(node_id) % N` 進行分配，每個測試的歸屬獨立穩定，不受其他測試增減影響 |
| **Duration-based 分配** | 使用 `.test_durations` 檔案（與 pytest-split 格式相容）進行貪婪 bin-packing，最小化最慢 shard 的執行時間 |
| **零設定** | 只需加上 `--shard-id` 與 `--num-shards` 參數，無需設定檔，也不需調整測試順序 |
| **最細粒度分配** | 以個別測試為單位切分，而非以檔案或 class 為單位 |
| **CI 平台無關** | 支援 GitHub Actions、CircleCI、Travis CI 或任何能執行平行 job 的系統 |
| **Allure 整合** | 各 shard 分別收集結果後合併，在統一的 Timeline 報告中檢視平行執行情況 |

## 文件

| 指南 | 說明 |
|------|------|
| [分派模式指南](doc/sharding-modes.zh-TW.md) | 詳細說明 `roundrobin`、`hash`、`duration` 的行為、`.test_durations` 用法、verbose shard 報告，以及模式選擇策略。 |
| [Demo Sessions](doc/demo-sessions.zh-TW.md) | 說明貢獻者如何用 `nox` 執行內建 demo 測試。 |
| [Allure Report 整合指南](doc/allure-integration.zh-TW.md) | 說明如何跨 shard 收集 Allure 結果並合併成單一報告、在本機平行執行 shard，以及整合 GitHub Actions / CircleCI。包含 30 個測試、3 個平行 shard 的完整範例與 Timeline 截圖。 |

## 快速開始

### 安裝

```bash
pip install pytest-shard-cloudc
```

### 將測試分散到 N 台機器

```bash
# 機器 0
pytest --shard-id=0 --num-shards=3

# 機器 1
pytest --shard-id=1 --num-shards=3

# 機器 2
pytest --shard-id=2 --num-shards=3
```

每台機器執行約 1/N 的測試，全部合計覆蓋 100% 的測試案例。

### 選擇分配模式

```bash
# Round-robin（預設）— 保證數量平衡
pytest --shard-id=0 --num-shards=3 --shard-mode=roundrobin

# Hash — 每個測試的歸屬穩定，無狀態
pytest --shard-id=0 --num-shards=3 --shard-mode=hash

# Duration — 依歷史執行時間做 bin-packing，最小化最慢 shard
pytest --shard-id=0 --num-shards=3 --shard-mode=duration --durations-path=.test_durations
```

更完整的模式比較、`.test_durations` 產生方式與選擇建議，請參考 [分派模式指南](doc/sharding-modes.zh-TW.md)。

### GitHub Actions 範例

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        shard_id: [0, 1, 2]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install pytest-shard-cloudc
      - run: pytest --shard-id=${{ matrix.shard_id }} --num-shards=3
```

### CircleCI 範例

```yaml
jobs:
  test:
    parallelism: 3
    steps:
      - run: pytest --shard-id=${CIRCLE_NODE_INDEX} --num-shards=${CIRCLE_NODE_TOTAL}
```

## 更多指南

- 如果你要看各模式的行為、`.test_durations`、verbose shard 報告與選擇策略，請參考 [分派模式指南](doc/sharding-modes.zh-TW.md)。
- 如果你要執行內建 demo，請參考 [Demo Sessions](doc/demo-sessions.zh-TW.md)。
- 如果你要產生報告與查看平行執行截圖，請參考 [Allure Report 整合指南](doc/allure-integration.zh-TW.md)。

## 替代方案

[pytest-xdist](https://github.com/pytest-dev/pytest-xdist) 可在單一機器上跨 CPU 核心平行執行測試，也支援遠端 worker。常見的搭配方式是：以 `pytest-shard` 將工作分散到各 CI node，再以 `pytest-xdist` 在各 node 內部進行核心級平行化。

[pytest-circleci-parallelized](https://github.com/ryanwilsonperkin/pytest-circleci-parallelized) 依據測試執行時間而非測試數量進行分配，但粒度僅到 class 層級，且僅支援 CircleCI。

## 貢獻

歡迎任何形式的貢獻。套件需求為 Python 3.11 以上。安裝開發工具鏈後執行完整檢查：

```bash
pip install -e .[dev]
nox
```

Allure 整合測試另需在 `PATH` 中提供 `allure` CLI。

## 授權

MIT 授權。

原始著作 Copyright 2019 Adam Gleave。
修改部分 Copyright 2026 Cloud Chen。
