"""webui：审阅工作台（人工决策 UI 的呈现层）。

当前实现是**零依赖的纯函数渲染器**：`review_workbench.render_review_workbench`
生成静态单文件 HTML，用回环静态服务（`python -m http.server --directory <服务根>`）
或直接开文件即可检视。包内没有 flask、没有路由、没有 `[project.scripts]` 入口，
flask 也已不在运行依赖里；`tests/test_webui_review_workbench.py` 的
`test_workbench_module_never_imports_flask_or_gates_upward` 以 AST 断言钉死这一点。

将来真要加 HTTP 层（路由、模板、静态资源），落点仍是本包，且不改这条硬线：
判定逻辑一律下沉 examcard.gates，webui → gates 是合法向下依赖，反向禁止。
"""
