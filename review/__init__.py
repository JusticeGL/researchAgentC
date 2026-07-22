"""LLM 审稿。见 IMPLEMENTATION-P4.md §5。

本期新增不变式：
  I26  没有 locator 的审稿意见直接丢弃（Objection.locator 必填且必须能在论文里定位）

关键约束：
  - review 的 schema 里**没有 score 字段**：不打总分、不存分、不展示分（§5.1）。
  - autocheck 是本模块唯一真正有价值的部分：把 checkable 意见转成断言自动核查。
  - panel 里三个模型各审一遍、互不相看，合并去重后进 review_comments 卡点（§5.3）。
"""
