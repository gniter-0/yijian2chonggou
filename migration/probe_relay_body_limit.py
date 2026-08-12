"""中继请求体上限二分探测（诊断工具，20260812 pilot step2a 全量 502 排障用）。

现象：探针/step2b（<10KB）全成功；step2a（113-250KB）全 502。
方法：strict json_schema 回显探针 + 递增垫料，定位 502 分界；每次调用近零成本。

运行：uv run python migration/probe_relay_body_limit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = Path(r"C:\Users\liushaocheng\Desktop\getinfo\yijian2")
sys.path.insert(0, str(NEW_ROOT / "src"))

from examcard.knowledge.llm import OpenAICompatibleStep2Transport  # noqa: E402
from examcard.pipeline.shadow_wiring import load_env_file  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def main() -> int:
    env = load_env_file(OLD_ROOT / ".env")
    transport = OpenAICompatibleStep2Transport(
        base_url=env["API_BASE_URL"], api_key=env["API_KEY"], timeout=180
    )
    model = env["TEXT_MODEL"]
    results = []
    for kb in (12, 40, 70, 100, 130, 180, 260):
        filler = "填" * (kb * 1024 // 3)
        content = (
            "以下为体积测试垫料，请完全忽略其内容，只返回 {\"ok\": true} 这一个 JSON 对象。\n" + filler
        )
        outcome: dict[str, object] = {"kb": kb, "content_utf8_kb": round(len(content.encode('utf-8')) / 1024, 1)}
        try:
            completion = transport.probe_json_schema(
                model=model,
                schema_name="body_limit_probe",
                response_schema=SCHEMA,
                user_content=content,
                max_tokens=64,
            )
            parsed = json.loads(completion.content)
            outcome.update({"result": "success", "http": completion.http_status, "ok": parsed.get("ok")})
        except Exception as error:  # 诊断工具：逐级登记错误形态即可
            status = getattr(error, "status_code", None)
            outcome.update({"result": type(error).__name__, "http": status})
        results.append(outcome)
        print(json.dumps(outcome, ensure_ascii=False))
        if outcome["result"] != "success" and len([r for r in results if r["result"] != "success"]) >= 2:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
