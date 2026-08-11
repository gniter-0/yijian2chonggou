"""utils：非底座的跨包杂项工具（writeback / lineage / 日志、确定性表格渲染 table_render、路径助手等，不冻结）。

与 common/ 的分界（总指挥终裁）：distill 四桶共用的冻结底座定式（hashing/GateCheck）→ common；
其余跨包杂项 → utils。不收任何桶特有类型。
红线：叶包——utils 不得 import 任何 examcard 内部包，防依赖环。
旧契约法定哈希/指纹同样禁止折叠进本包（铁律 bc-b8b9aca6，逐字节随包搬运）。
"""
