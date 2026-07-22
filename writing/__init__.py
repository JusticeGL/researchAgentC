"""写作、消融与交付。见 IMPLEMENTATION-P3.md。

第一/二期的 I1–I15、禁止事项全部继续生效。本期新增：
  I16 强论断必须注册且必须有 source（claim registry + checker 硬失败）
  I17 claim 的 source 只能是契约字段 / 预注册消融 / 经卡点批准的新 claim —— 没有第四种
  I18 负面结果必须可写且走完整流程
  I19 每张图可重跑且 byte-identical
  I20 消融必须预注册或经批准
  I21 unverifiable 引用不得被静默放行

本期目标：让系统能写论文，但**写不出契约里没有的结论**。
"""
