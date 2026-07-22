# <<TITLE>>

## Abstract

<<ABSTRACT_FIRST_SENTENCE>>
<!-- agent-fill: 用 2-3 句说明这是负面结果、为何仍有价值。不得粉饰成正面结论。 -->

## Introduction

### 假设与当时的理由

我们预注册的假设是：<<HYPOTHESIS>>。当时认为它合理的依据：<<NOVELTY_NOTE>>。
<!-- agent-fill: 展开动机。 -->

## Method

<!-- agent-fill: 方法描述（与正面论文一致，因为搜索过程相同）。 -->

## Results：假设未成立

本次搜索共进行 <<N_NODES>> 个实验节点，消耗预算约 <<BUDGET_USED>>。

<<DEV_TEST_GAP>>
<!-- 若 dev 上确认、test 上未成立：此处量化 dev/test gap，指出这是搜索过拟合的证据。 -->

### 被排除的方向

以下方向经实验被排除（来自经验台账 deadend 条目）：

<<DEADEND_DIRECTIONS>>

## Limitations

<<LIMITATIONS>>
<!-- 必填。fluke 记录与 deadend 台账在此成为素材。 -->

## Reproducibility

全部数字可由 `make reproduce` 复现。contract_hash: <<CONTRACT_HASH>>。
