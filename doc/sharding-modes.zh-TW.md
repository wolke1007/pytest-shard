**繁體中文** | [English](sharding-modes.md)

# 分派模式

本指南說明 `pytest-shard` 各種 `--shard-mode` 的行為、如何產生 `.test_durations`，以及實務上該如何選擇模式。

## 可用模式

透過 `--shard-mode` 可選擇三種模式：

### `roundrobin`（預設）

將測試依 node ID 排序後，以索引輪流分配：

```
shard_id = 排序後的索引 % num_shards
```

```mermaid
flowchart LR
    A[test_a] --> S0[Shard 0]
    B[test_b] --> S1[Shard 1]
    C[test_c] --> S2[Shard 2]
    D[test_d] --> S0
    E[test_e] --> S1
    F[test_f] --> S2
```

- 各 shard 的測試數量差距**不超過 1**，無論測試總數為何。
- 每次執行確定性，但新增或移除測試時，其他測試的分配可能隨著排序順序改變。

### `hash`

```
shard_id = SHA-256(test_node_id) % num_shards
```

```mermaid
flowchart LR
    A[test_a] --> H0[hash mod 3 = 2]
    B[test_b] --> H1[hash mod 3 = 0]
    C[test_c] --> H2[hash mod 3 = 2]
    D[test_d] --> H3[hash mod 3 = 1]

    H0 --> S2[Shard 2]
    H1 --> S0[Shard 0]
    H2 --> S2
    H3 --> S1[Shard 1]
```

- 每個測試的歸屬**獨立穩定**，新增或移除其他測試不影響既有測試的分配。
- 無狀態，不需要額外檔案。
- 測試數量較少時分配可能不均。

### `duration`

使用 `.test_durations` JSON 檔案（與 [pytest-split](https://github.com/jerry-git/pytest-split) 格式相容），記錄每個 node ID 的執行時間（秒）：

```json
{
  "tests/test_foo.py::test_slow": 4.2,
  "tests/test_foo.py::test_fast": 0.1
}
```

採用**最長工作優先（LPT）**貪婪演算法：依執行時間由長到短排序，依序分配給當前累計時間最短的 shard。沒有紀錄的測試預設為 1.0 秒。

```mermaid
flowchart LR
    A[test_slow 10s] --> S0[Shard 0 total 10s]
    B[test_mid 6s] --> S1[Shard 1 total 6s]
    C[test_mid 5s] --> S2[Shard 2 total 5s]
    D[test_fast 2s] --> S2b[Shard 2 total 7s]
    E[test_fast 1s] --> S1b[Shard 1 total 7s]
    F[test_fast 1s] --> S0b[Shard 0 total 11s]
```

## 產生 `.test_durations`

使用 `--store-durations` 可記錄每個測試在 call phase 的執行時間，並在 session 結束時寫入檔案：

```bash
# 寫入目前目錄下的 .test_durations
pytest tests --store-durations

# 指定自訂輸出路徑
pytest tests --store-durations --durations-path=artifacts/test_durations.json
```

- `--store-durations` 會為本次執行啟用 duration 記錄。
- `--durations-path=PATH` 用來控制 JSON 檔案的讀寫位置；預設是 `.test_durations`。
- 檔案中原有的紀錄會保留；本次執行到的測試只會覆寫自己的項目。
- 如果是平行 shard 執行，建議每個 shard 先寫到各自的檔案，再合併後給 `--shard-mode=duration` 使用。

## Verbose shard 報告

預設情況下，pytest 會在收集階段印出一行摘要：

```
Running 7 items in this shard (mode: roundrobin)
```

加上 `-v` 後，會額外列出該 shard 分配到的所有測試 node ID：

```
Running 7 items in this shard (mode: roundrobin): tests/test_foo.py::test_a, ...
```

## Duration 模式的前置條件

`--shard-mode=duration` 需要 `--durations-path` 指向的檔案事先存在。
如果檔案不存在，請先用 `--store-durations` 跑一次一般測試，例如：

```bash
pytest tests --store-durations --durations-path=.test_durations
pytest tests --shard-mode=duration --durations-path=.test_durations --num-shards=3 --shard-id=0
```

## 模式比較

| 模式 | 數量平衡 | 時間平衡 | 需要資料檔 | 每測試穩定 |
|------|:---:|:---:|:---:|:---:|
| `roundrobin` | ✓（精確） | — | — | — |
| `hash` | △（小樣本） | — | — | ✓ |
| `duration` | — | ✓（最佳化） | ✓ | — |

## 該選哪一種模式？

- 如果你想要最穩妥的預設行為，並希望各 shard 的測試數量大致平均，選 `roundrobin`。
- 如果你更在意每個測試的分配穩定性，而不是各 shard 的測試數量完全平均，例如希望測試集增減時某個既有測試仍留在同一個 shard，選 `hash`。
- 如果測試執行時間差異很大，而且你更在意整體 wall-clock time 而不是每個 shard 的測試數量，選 `duration`。在成熟的 CI pipeline 中，只要你已有有效的 `.test_durations` 檔案，通常這會是最佳選項。
