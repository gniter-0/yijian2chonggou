# exam-card-engine（examcard）

考试知识图解卡片生成引擎：yijian2 V2 内核（knowledge_v2 / evidence_v2 / materials_v2）的抽包新仓。
四步流水线：素材接入 → 证据/知识 → 卡片编译 → 图像编译，门禁与人工审阅贯穿其中。

- 发行名/导入名：`examcard`（src 布局，uv 管理，可安装包）
- 工程规范（pyproject 决策、目录职责、测试隔离 T1–T8）：`docs/engineering_skeleton.md`
- 自旧仓迁移的模块去向清单：`docs/extraction_manifest.md`

## 快速开始

```bash
uv sync                                            # 建 .venv 并按 uv.lock 安装（含 dev 组 pytest）
uv run pytest --basetemp .test-tmp/full            # 默认套件：离线、密闭（live 用例自动剔除）
uv run pytest --basetemp .test-tmp/live -m live    # 显式运行真实外呼用例（需自备密钥，见规范 T6）
```

> **`--basetemp .test-tmp/<名字>` 是强制项**：本仓每一次 pytest 调用（本地、CI、文档示例、脚本）都必须显式带上。缺省 basetemp 落在 `%TEMP%\pytest-of-<用户名>`，本机该目录 ACL 已损，裸跑会刷出上百条 `tmp_path` fixture 初始化 ERROR（环境缺陷，非代码缺陷），整轮判定作废。规则与病因见 `docs/engineering_skeleton.md` §4.1，管理员一行修复命令见 §5.1。

## 目录一览

```
src/examcard/
  materials/    素材接入（原 materials_v2 整包）
  evidence/     类型化证据（原 evidence_v2 整包）
  knowledge/    语义知识内核（contracts/continuation/diagram/planner + 核心模块）
  compilers/    Step3/4 卡片与图像编译器
  gates/        门禁判定（review 人工准入 / release 发布就绪）
  pipeline/     四步流水线编排（出网走顶层 providers）
  providers/    供应商兼容层（顶层包，knowledge/compilers/pipeline 共用）
  webui/        审阅工作台（纯函数渲染静态单文件 HTML，无 flask/无 HTTP 层）
  common/       四 distill 桶共用冻结底座（hashing/gates/io/freeze/junit/schema 六模块，签名冻结）
  utils/        非底座跨包杂项工具（两者均为叶包，不 import 任何内部包）
tests/          pytest 套件（golden 黄金样本 / fixtures 数据装置）
docs/           工程规范与迁移清单
migration/      抽包辅助脚本（一次性工具）
```
