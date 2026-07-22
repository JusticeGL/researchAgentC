# <<TITLE>>

## Abstract

<<ABSTRACT_FIRST_SENTENCE>>
<!-- agent-fill: 用 2-3 句解释性文字概述方法与结果。不得提出契约里没有的结论；数字必须用数值模板，引用必须用文献 key（此注释里刻意不写模板语法，避免被渲染器当成真模板）。 -->

## Introduction

<!-- agent-fill: 背景与动机（来自契约 question / hypothesis）。 -->

## Method

数据集 <<DATASET>>，评测协议 <<SPLIT>>。
<!-- agent-fill: 方法描述。 -->

## Results

在预注册的 <<SPLIT>> 协议下，本方法将 <<METRIC>> 从 {{agg:baseline.acc}} 提升到 {{agg:main.acc}} [claim:primary]。

<!-- agent-fill: 对结果表的解释性描述。所有数字必须来自模板替换。 -->

### 消融

<<ABLATION_SECTION>>

## Limitations

<<LIMITATIONS>>

## Reproducibility

全部数字可由 `make reproduce` 复现。contract_hash: <<CONTRACT_HASH>>。
