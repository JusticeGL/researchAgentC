"""确定性图表。见 IMPLEMENTATION-P3.md §4.4。

I19：每张图可重跑且 byte-identical。做不到就改图，**不放宽检查**。
每张图一个目录：plot.py（确定性脚本）+ data.json（来自结果库的数字快照）
+ manifest.json（script_sha / run_ids / contract_hash / output_sha256）+ out.pdf。
"""
