"""实验搜索循环。见 IMPLEMENTATION-P2.md。

第一期的不变式（I1–I8）、禁止事项全部继续生效，本期只增不减：
  I9  agent 只能写 solution/；harness/ core/ contracts/ 只读（容器 mount + 每步 diff 双保险）
  I10 每个树节点至少对应一条 run 记录
  I11 命中阈值不得终止搜索（SearchVerdict 里根本没有 DONE）
  I12 与历史提案高度相似的改动不得直接执行（dedup 闸门在 interpreter 之前）
  I13 训练期出站网络受限
  I14 进入新 run 的上下文不含全量日志（build_context 有 token 上限断言）
  I15 台账压缩不得丢失证据
"""
