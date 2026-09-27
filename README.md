# Memory Evolution Engine

**A framework-independent Python library for the lifecycle of agent experiences.** It helps applications decide which lessons from failures, successes, feedback, and confirmation remain useful over time.

[English](#english) · [中文](#中文)

## English

### Overview

Memory Evolution Engine manages experience records rather than collecting conversations. An application supplies experiences and observed outcomes. The library stores them, tracks evidence of value, applies deterministic reinforcement and decay, and identifies candidates for archival.

Version 0.1.0 is an embedded Python 3.11+ package. It uses SQLite and has no runtime dependencies outside the standard library. It does not run an agent or call a model.

### Why

Saving every interaction can bury a useful lesson under unrelated history. An agent may instead need to recognize a familiar failure and retrieve the action that prevented it. `failure_pattern` records what went wrong; `lesson` records what to do differently. Applications can link the two through `related_to`.

The library makes subsequent decisions explicit: how recently an experience was useful, what evidence supports its long-term value, and when to review it for archival. These rules are deterministic and configurable; they do not infer success or failure automatically.

### Features

- **Scoped experiences:** `Experience` supports `fact`, `preference`, `goal`, `procedure`, `event`, `failure_pattern`, and `lesson`. ID-based operations require a `scope`.
- **Impact evidence:** `ImpactEvidence` records user feedback, a prevented failure, repeated success, or manual confirmation. A source and reference identify an event; without a reference, the source and normalized description are used to reject duplicate evidence for the same experience.
- **Separate lifecycle signals:** `strength` represents recent activity and changes through reinforcement and decay. `impact` represents long-term value and changes when evidence is added. `confidence` represents trust in the content.
- **Deterministic maintenance:** `maintain()` reports archival candidates without changing them by default. Set `dry_run=False` to archive them explicitly.
- **Transactional writes:** reinforcement, evidence updates, and explicit archival batches roll back on failure.

### Architecture

```text
Application or agent framework
          │ supplies experiences and observed outcomes
          ▼
     MemoryEngine
       ├─ LifecyclePolicy: scoring, reinforcement, decay, thresholds
       └─ SQLiteStorage: experiences and impact evidence
```

The engine does not depend on an agent framework. `remember()` stores a supplied experience; `recall()` searches within a scope; `reinforce()` updates only strength; `add_impact_evidence()` records a distinct observation and recalculates impact. `forget()` removes an experience and its evidence. New experiences must start with `impact=0`.

The default policy uses:

```text
decayed_strength = strength × 2 ^ (-elapsed_days / half_life_days)
impact = min(1, sum(evidence weights) × evidence_impact_step)
effective_score = confidence × (impact_prior + (1 - impact_prior) × impact)
                  × decayed_strength
```

The default `impact_prior` is `0.5`, so an unconfirmed new experience does not start with an effective score of zero. This prior is a ranking rule, not impact evidence. Set it to `0` for a purely multiplicative score. Reinforcement adds a configurable step to the *decayed* strength, capped at 1; it does not change impact.

### Quick Start

Install from a local checkout:

```bash
python -m pip install .
```

```python
from memory_evolution_engine import Experience, MemoryEngine, SQLiteStorage

with SQLiteStorage("memory.db") as storage:
    engine = MemoryEngine(storage)
    engine.remember(Experience(
        scope="agent:demo",
        content="Check whether an operation is idempotent before retrying it.",
        kind="lesson",
    ))
    print(engine.recall(scope="agent:demo", text="idempotent"))
```

For local development, install the optional test dependency with `python -m pip install -e ".[test]"`, then run `python -m pytest`.

### Example

Record a failure, link a lesson, then add evidence when that lesson prevents the failure from recurring:

```python
from memory_evolution_engine import (
    Experience, ExperienceKind, ImpactEvidence, ImpactSource,
    MemoryEngine, SQLiteStorage,
)

scope = "agent:payments"
with SQLiteStorage("agent-memory.db") as storage:
    engine = MemoryEngine(storage)
    failure = engine.remember(Experience(
        scope=scope,
        kind=ExperienceKind.FAILURE_PATTERN,
        content="Retrying a charge without an idempotency key caused a duplicate payment.",
    ))
    lesson = engine.remember(Experience(
        scope=scope,
        kind=ExperienceKind.LESSON,
        content="Check idempotency before retrying a charge.",
        related_to=(failure.id,),
    ))

    # The application observed a concrete outcome; the engine does not infer it.
    engine.add_impact_evidence(
        lesson.id,
        ImpactEvidence(
            source=ImpactSource.PREVENTED_FAILURE,
            description="Avoided a duplicate charge on a later retry.",
            reference="payment-task-42",
        ),
        scope=scope,
    )
    engine.reinforce(lesson.id, scope=scope)

    related = engine.recall(scope=scope, text="duplicate payment", include_related_lessons=True)
    report = engine.maintain(scope=scope)  # Dry run: no records are archived.
```

### Limitations

- Retrieval uses SQLite literal substring matching and in-memory ranking of matching records. It is not semantic search; large-scale retrieval is outside v0.1. Related lessons may extend the result beyond the primary `limit`.
- `scope` isolates records but does not authenticate callers. The application must enforce access control. Use one `SQLiteStorage` connection per thread. v0.1 targets a single process and low write concurrency, not multiple high-throughput workers.
- Evidence deduplication uses a stable ID and a per-experience fingerprint. Distinct references can still describe the same real-world event. Duplicate submissions raise `sqlite3.IntegrityError`. Evidence can be added through the engine's public API; there is no public operation to delete individual evidence. `forget()` deletes the entire experience and its evidence.
- The frozen `Experience` dataclass has mutable JSON `metadata`, including nested containers. Do not mutate it after storage; create an updated copy when changing it.
- SQLite uses `PRAGMA user_version=2`. Back up a database before upgrading. A newer schema is rejected; older data with duplicate evidence under the new fingerprint rule requires manual review before migration completes.
- The v0.1 API may change. The library does not extract experiences from conversations, invoke an LLM, or provide an agent runtime.

## 中文

### 概览

Memory Evolution Engine 是一个与 Agent 框架无关的 Python **经验生命周期**库。应用负责提交经验、判断任务结果；库负责保存经验、记录价值证据、按确定性规则强化和衰减，并找出需要考虑归档的记录。它管理的是经验，而非自动收集聊天记录。

v0.1.0 支持 Python 3.11+，默认使用 SQLite，运行时只依赖 Python 标准库。它不运行 Agent，也不调用模型。

### 为什么需要它

保存所有交互并不保证下一次能找到有用的教训。Agent 可能需要识别曾经出现的失败，并找到避免重犯的做法。`failure_pattern` 记录失败方式，`lesson` 记录应该改变的行为；两者可通过 `related_to` 关联。

库把后续判断拆成明确规则：经验最近是否被使用、有哪些证据说明它具有长期价值、何时应进入归档候选。规则可配置且结果确定；库本身不会自动推断任务成功或失败。

### 功能

- **按 scope 隔离经验：**`Experience` 支持 `fact`、`preference`、`goal`、`procedure`、`event`、`failure_pattern`、`lesson`。按 ID 操作必须提供 `scope`。
- **记录价值证据：**`ImpactEvidence` 支持用户反馈、避免失败、重复成功和人工确认。相同来源与引用会被识别为重复；没有引用时，以来源和规范化描述去重。
- **区分三个指标：**`strength` 表示近期活跃程度，受强化与衰减影响；`impact` 表示长期价值，仅随新增证据变化；`confidence` 表示内容可信程度。
- **确定性的维护：**`maintain()` 默认只生成归档候选报告；显式设置 `dry_run=False` 才执行归档。
- **事务写入：**强化、证据更新和显式批量归档发生异常时会回滚。

### 架构

```text
应用或 Agent 框架
       │ 提交经验和已观察到的结果
       ▼
  MemoryEngine
    ├─ LifecyclePolicy：评分、强化、衰减、阈值
    └─ SQLiteStorage：经验与价值证据
```

库不绑定任何 Agent 框架。`remember()` 保存应用提供的经验；`recall()` 在指定 scope 内检索；`reinforce()` 只更新 strength；`add_impact_evidence()` 保存独立的观察证据并重新计算 impact；`forget()` 删除经验及其证据。新经验必须以 `impact=0` 开始。

默认策略为：

```text
衰减后强度 = strength × 2 ^ (-经过天数 / 半衰期天数)
impact = min(1, 证据权重之和 × evidence_impact_step)
有效分数 = confidence × (impact_prior + (1 - impact_prior) × impact)
           × 衰减后强度
```

`impact_prior` 默认是 `0.5`，让尚未收到反馈的新经验不至于从零分开始。它只是排序先验，不是价值证据；设为 `0` 可使用纯乘法评分。强化会在**已衰减的强度**上增加可配置步长，最高为 1，不改变 impact。

### 快速开始

在本地项目目录安装：

```bash
python -m pip install .
```

```python
from memory_evolution_engine import Experience, MemoryEngine, SQLiteStorage

with SQLiteStorage("memory.db") as storage:
    engine = MemoryEngine(storage)
    engine.remember(Experience(
        scope="agent:demo",
        content="重试操作前先确认它是否具备幂等性。",
        kind="lesson",
    ))
    print(engine.recall(scope="agent:demo", text="幂等性"))
```

本地开发可运行 `python -m pip install -e ".[test]"` 安装可选测试依赖，再运行 `python -m pytest`。

### 示例

记录失败、关联教训，并在教训确实避免重复失败后添加证据：

```python
from memory_evolution_engine import (
    Experience, ExperienceKind, ImpactEvidence, ImpactSource,
    MemoryEngine, SQLiteStorage,
)

scope = "agent:payments"
with SQLiteStorage("agent-memory.db") as storage:
    engine = MemoryEngine(storage)
    failure = engine.remember(Experience(
        scope=scope,
        kind=ExperienceKind.FAILURE_PATTERN,
        content="没有幂等键就重试扣款，导致重复支付。",
    ))
    lesson = engine.remember(Experience(
        scope=scope,
        kind=ExperienceKind.LESSON,
        content="重试扣款前检查幂等性。",
        related_to=(failure.id,),
    ))

    # 应用观察到一次具体结果后，提交价值证据。
    engine.add_impact_evidence(
        lesson.id,
        ImpactEvidence(
            source=ImpactSource.PREVENTED_FAILURE,
            description="后续重试避免了重复扣款。",
            reference="payment-task-42",
        ),
        scope=scope,
    )
    engine.reinforce(lesson.id, scope=scope)

    related = engine.recall(scope=scope, text="重复支付", include_related_lessons=True)
    report = engine.maintain(scope=scope)  # 默认预览，不执行归档。
```

### 当前限制

- 检索使用 SQLite 字面子串匹配，并在内存中对匹配记录排序；不支持语义检索，v0.1 也不面向大规模检索。关联返回的 lesson 可能使结果数量超过主查询的 `limit`。
- `scope` 提供数据隔离，不负责调用方身份验证；应用必须实施访问控制。每个线程使用一个 `SQLiteStorage` 连接。v0.1 面向单进程、低写入并发，不承诺多 worker 高吞吐写入。
- 证据通过稳定 ID 和每条经验内的指纹去重，但不同引用仍可能指向同一真实事件。重复提交会抛出 `sqlite3.IntegrityError`。公开 API 只支持通过引擎添加证据，不支持单独删除某条证据；`forget()` 会删除整条经验及其证据。
- `Experience` 是冻结 dataclass，但 JSON 格式的 `metadata` 及其嵌套容器仍可变。存储后不要直接修改，应创建更新后的副本。
- SQLite 使用 `PRAGMA user_version=2`。升级前请备份数据库。版本更新的 schema 会被拒绝；旧数据若含按新指纹规则判定的重复证据，需要先人工检查才能完成迁移。
- v0.1 API 仍可能调整。库不从对话中自动抽取经验，不调用 LLM，也不提供 Agent 运行时。
