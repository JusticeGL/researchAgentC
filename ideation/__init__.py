"""选题流水线。见 IMPLEMENTATION-P4.md §4。

本期新增不变式（累加在 I1–I21 之上）：
  I22  填不满契约必填字段的想法不得进入候选池（fill.py 输出 incomplete，不进排名）
  I23  新颖性判定必须基于检索（novelty_evidence 非空且每条能在 corpus 解析出 ID）
  I24  生成器之间互不可见（独立调用 + 独立 context，代码层面无共享 state）
  I25  可行性打分对照真实资源（真实 run 时长来自结果库，数据集规模来自 harness/MOABB）
  I27  不计算、不存储、不展示聚合评分（schema 里没有这个字段；五轴永不合并）

去重直接复用第二期 loop/dedup.py，不写第二套（§4.2）。
LLM 参与的步骤一律以依赖注入的可调用对象传入（离线可测；live 需真实模型 key）。
"""
