# <<TITLE>>

## Abstract

<<ABSTRACT_FIRST_SENTENCE>>
<!-- agent-fill: 说明为何无结论（预算耗尽且未完成确认），以及已排除/未排除的范围。 -->

## Introduction

### 假设

我们预注册的假设是：<<HYPOTHESIS>>。理由：<<NOVELTY_NOTE>>。

## Method

<!-- agent-fill: 方法与搜索设置。 -->

## Results：未能得出结论

搜索在预算耗尽前未完成确认协议，测试集**从未被开启**（holdout_access 应为 0）。
本次共进行 <<N_NODES>> 个实验节点，消耗预算约 <<BUDGET_USED>>。

<!-- agent-fill: 说明搜索到达的状态、以及若继续需要多少额外预算的估计。 -->

## Limitations

<<LIMITATIONS>>

## Reproducibility

全部数字可由 `make reproduce` 复现。contract_hash: <<CONTRACT_HASH>>。
