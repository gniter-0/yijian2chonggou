# knowledge_v2 抽包清点清单（extraction manifest v3）

- 生成日期：2026-08-12；方法：正则扫描 import 图（只读）
- 扫描范围：yijian2/scripts 全部 .py（含 tests/tools）；对象：knowledge_v2 下 142 个模块
- v2：已按复核根因修正——RC1 迁走 import 闭包自动上提 V25 运行时模块；RC2 测试按 import 闭包判归属。详见文末『七』。
- v3：RC3 施工实测回填——`learning_fixture` / `step2b_card_golden` 属运行时 import 闭包，路由改落 `src/examcard/knowledge/` 包内（不入 tests/）；补记扫描盲区（缩进式惰性 import、动态字符串导入）。详见文末『七』。

## 总览

| 大类 | 模块数 | 体量 | 去向 |
|---|---|---|---|
| core | 19 | 762KB | 迁走·新仓 |
| core-current | 2 | 107KB | 迁走·新仓(V25 现役) |
| current-runtime | 5 | 188KB | 迁走·新仓(V25 运行时闭包·RC1 上提) |
| historical | 6 | 220KB | 留旧仓 |
| phase | 110 | 5322KB | 留旧仓(distill 提炼/archive 归档) |
| 合计 | 142 | 6599KB | — |

> 迁走(core+core-current+current-runtime)= **26 个**；留旧仓(historical+phase)= **116 个**。

## 一、核心模块 → 新仓 src/examcard（19 个，762KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| semantic_contracts | 380KB | 52/1/29/16/38/0 | src/examcard/knowledge/contracts/*  (389KB 在此拆分) |
| fact_engine | 67KB | 6/0/1/3/2/1 | src/examcard/knowledge/fact_engine.py + fact_*.py  (77KB，cm21 拆分：门面 + 12 兄弟) |
| semantic_llm | 64KB | 51/1/6/7/52/0 | src/examcard/knowledge/llm.py + llm_*.py  (77KB，cm25 拆分：门面 + 7 兄弟) |
| learning_fixture | 38KB | 7/1/7/0/7/0 | src/examcard/knowledge/learning_fixture.py  (黄金样本装置·RC3 路由修正：被 V25 运行时闭包顶层 import) |
| table_continuation_integration | 31KB | 10/0/0/0/12/0 | src/examcard/knowledge/continuation/integration.py |
| continuation_review | 30KB | 11/1/2/5/11/0 | src/examcard/knowledge/continuation/review.py |
| table_continuation | 29KB | 2/1/0/1/2/0 | src/examcard/knowledge/continuation/core.py |
| semantic_projection | 28KB | 10/0/4/1/11/0 | src/examcard/knowledge/projection.py |
| teaching_quality | 16KB | 2/1/6/3/0/0 | src/examcard/knowledge/quality.py |
| table_continuation_request_fixture | 14KB | 3/1/0/2/5/0 | tests/fixtures/ |
| schema | 13KB | 28/1/8/20/4/0 | src/examcard/knowledge/schema.py |
| step2b_card_golden | 11KB | 4/1/0/4/2/0 | src/examcard/knowledge/step2b_card_golden.py  (RC3 路由修正：被 continuation/core.py 函数内惰性 import) |
| table_repair_sidecar | 10KB | 6/1/4/1/5/0 | src/examcard/knowledge/continuation/repair_sidecar.py |
| v1_adapter | 9KB | 1/0/0/1/1/1 | src/examcard/knowledge/adapter_v1.py |
| diagram_sidecar | 8KB | 6/1/6/5/11/0 | src/examcard/knowledge/diagram/sidecar.py |
| diagram_disposition | 5KB | 2/1/6/4/9/0 | src/examcard/knowledge/diagram/disposition.py |
| step2b_bridge | 5KB | 1/0/0/0/1/0 | src/examcard/knowledge/planner/bridge.py |
| state_machine | 2KB | 1/0/0/1/0/0 | src/examcard/knowledge/state_machine.py |
| identity | 2KB | 1/0/0/3/0/0 | src/examcard/knowledge/identity.py |

## 二、现役契约（V25 声明层）→ 新仓（2 个，107KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| v25_generic_contracts | 101KB | 16/1/20/0/1/0 | src/examcard/knowledge/contracts/ (V25 现役契约)  (cm28 拆分：门面 + 15 兄弟 v25_*.py) |
| v25_teaching_gate_layers | 6KB | 1/1/9/0/0/0 | src/examcard/knowledge/contracts/ (V25 现役契约) |

## 二-2、V25 运行时闭包（RC1·import 闭包上提·必须随现役契约迁走）（5 个，188KB）

> 这些模块被迁走集的 import 闭包拉入（起点：`v25_teaching_gate_layers → phase2r142_v25_projection`），是 V25 现役契约的**运行时依赖**。旧版按命名把它们判成 archive/留旧仓会导致新仓 import 断（archive 安全性缺陷）。
> ⚠ 该簇内部相互成环（142_projection↔14_live↔12/13_review↔11_five_golden），说明**现役契约与黄金/复核脚手架发生耦合**；阶段2集成时建议解耦（把纯黄金样本落 tests/golden、契约运行时仅保留必要类型），但**当前为保 import 闭包必须整簇迁走**。

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 被谁拉入(直接 importer) | 去向 |
|---|---|---|---|---|
| phase2r13_v25_reconciliation | 45KB | 1/1/9/0/1/0 | phase2r14_v25_live | src/examcard/knowledge/contracts/ (V25 运行时闭包·随现役契约上提) |
| phase2r12_v25_golden_review | 42KB | 1/1/5/0/1/0 | phase2r13_v25_reconciliation, phase2r14_v25_live | src/examcard/knowledge/contracts/ (V25 运行时闭包·随现役契约上提) |
| phase2r142_v25_projection | 40KB | 12/0/14/1/1/0 | v25_teaching_gate_layers | src/examcard/knowledge/contracts/ (V25 运行时闭包·随现役契约上提) |
| phase2r14_v25_live | 37KB | 2/0/12/0/3/0 | phase2r142_v25_projection | src/examcard/knowledge/contracts/ (V25 运行时闭包·随现役契约上提) |
| phase2r11_generic_five_golden | 24KB | 1/1/1/0/1/0 | phase2r12_v25_golden_review | src/examcard/knowledge/contracts/ (V25 运行时闭包·随现役契约上提) |

## 三、历史台阶（v19/v23/v24）→ 留旧仓归档（6 个，220KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| v24_step2b_grouping_obligations | 55KB | 3/1/1/1/3/0 | 留旧仓 (v19/v23/v24 历史台阶) |
| v23_step2b_bridge | 47KB | 2/1/0/0/2/0 | 留旧仓 (v19/v23/v24 历史台阶) |
| v23_fact_card_reconciliation | 43KB | 2/1/0/1/2/0 | 留旧仓 (v19/v23/v24 历史台阶) |
| v19_learning_alignment | 30KB | 1/1/0/0/1/0 | 留旧仓 (v19/v23/v24 历史台阶) |
| v24_card_candidate_review | 23KB | 1/1/0/0/1/0 | 留旧仓 (v19/v23/v24 历史台阶) |
| v23_card_candidate_review | 22KB | 6/1/0/3/6/0 | 留旧仓 (v19/v23/v24 历史台阶) |

## 四、phase 脚本（110 个）

distill = 通用逻辑的提炼来源（逻辑进新仓对应包，脚本本体仍留旧仓）；archive = 原地归档。
注：原 v1 归在 phase 的 5 个 V25 运行时模块已按 RC1 上提至『二-2』，故本节较 v1 少 5 个。
注：标【A案】的行，其去向桶不变，但【跨桶消费的纯数据模型】另下沉 knowledge/contracts/admission_fixtures.py（+法定哈希搬运件 admission_legacy_hash.py），依据 docs/design/distill_gates_review_design.md §4/§5。

## 4.1 提炼源：compilers（Step3/4 编译器）（4 个，221KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| phase2r4_step4_generic_contract_compiler | 69KB | 1/1/3/0/1/0 | distill:compilers |
| phase2r9_generic_step3_card_compiler | 59KB | 1/0/10/0/2/0 | distill:compilers；【A案】跨桶数据模型 Step3CardContract → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r11_generic_step4_source_derived_image_compiler | 47KB | 1/0/0/0/3/0 | distill:compilers |
| phase2r92_generic_step3_response_contract | 45KB | 2/0/7/0/3/0 | distill:compilers |

## 4.2 提炼源：providers（供应商兼容层）（18 个，846KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| phase2r96_generic_step3_provider_envelope_profile_remediation | 73KB | 3/0/1/0/5/0 | distill:providers |
| phase2r112_generic_step4_provider_image_response_compatibility_remediation | 65KB | 2/0/1/0/3/0 | distill:providers |
| phase2r97_generic_step3_provider_envelope_profile_real_pilot | 64KB | 1/0/0/0/4/0 | distill:providers |
| phase2r95_generic_step3_provider_envelope_real_pilot | 56KB | 1/0/1/0/4/0 | distill:providers |
| phase2r2241_step3_source_text_remediation | 55KB | 1/1/2/0/1/0 | distill:providers |
| phase2r94_generic_step3_provider_envelope_compatibility_remediation | 53KB | 2/0/2/0/4/0 | distill:providers |
| phase2r116_gpt_image2_http_422_request_compatibility_remediation | 51KB | 2/0/5/0/2/0 | distill:providers |
| phase2r311_step4_card6_table_border_remediation | 49KB | 1/1/2/0/1/0 | distill:providers |
| phase2r36_step4_card2_connector_topology_remediation | 48KB | 1/1/2/0/1/0 | distill:providers |
| phase2r42_step4_card14_formula_remediation | 48KB | 1/0/1/0/2/0 | distill:providers |
| phase2r2232_step3_relay_diagnostic | 47KB | 1/1/2/0/1/0 | distill:providers |
| phase2r118_gpt_image2_http_502_source_backed_relay_remediation | 45KB | 2/0/0/0/2/0 | distill:providers |
| phase2r151_gonglu24_remediation_live | 34KB | 1/1/1/0/1/0 | distill:providers |
| phase2r2231_step3_relay_recovery | 34KB | 1/1/1/0/1/0 | distill:providers |
| phase2r34_step4_card2_exact_label_remediation | 33KB | 1/1/3/0/1/0 | distill:providers |
| phase2r32_step4_image_relay_recovery | 33KB | 1/1/1/0/1/0 | distill:providers |
| phase2r114_active_gpt_image2_relay_capability_remediation | 32KB | 3/0/4/0/2/0 | distill:providers |
| phase2r101_relay_baseline_compatibility | 24KB | 3/0/1/0/4/0 | distill:providers |

## 4.3 提炼源：gates/review（门禁与人工准入）（26 个，1516KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| phase2r83_generic_typed_evidence_review_workbench | 97KB | 1/0/1/0/4/0 | distill:gates-review |
| phase2r81_multi_subject_typed_evidence_admission | 95KB | 1/0/3/0/3/0 | distill:gates-review |
| phase2r85_production_human_source_page_decision_workbench | 89KB | 3/0/3/0/3/0 | distill:gates-review |
| phase2r87_production_source_page_human_decision_workbench | 88KB | 1/0/0/0/2/0 | distill:gates-review |
| phase2r810_r89_reviewed_admission_authorization | 84KB | 1/0/2/0/2/0 | distill:gates-review；【A案】跨桶数据模型 ReviewedTypedEvidenceFixture → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r84_selected_batch_human_decision_ingestion | 83KB | 5/0/6/0/6/0 | distill:gates-review |
| phase2r8_one_subject_typed_evidence_admission | 76KB | 1/0/1/0/4/0 | distill:gates-review |
| phase2r86_r85_production_human_decision_explicit_export | 67KB | 2/0/2/0/2/0 | distill:gates-review |
| phase2r73_generic_typed_evidence_review_queue | 58KB | 1/0/2/0/3/0 | distill:gates-review |
| phase2r10_generic_step3_human_review_admission | 58KB | 3/0/2/0/4/0 | distill:gates-review；【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r82_generic_review_decision_ingestion | 55KB | 1/0/1/0/3/0 | distill:gates-review |
| phase2r71_multi_subject_source_confirmed_evidence_admission | 54KB | 1/0/4/0/2/0 | distill:gates-review |
| phase2r316_step4_card3_reviewed_image_fixture | 54KB | 1/1/2/0/1/0 | distill:gates-review |
| phase2r313_step4_card6_reviewed_image_fixture | 54KB | 1/1/2/0/1/0 | distill:gates-review |
| phase2r319_step4_card4_reviewed_image_fixture | 54KB | 1/1/1/0/1/0 | distill:gates-review；【A案】跨桶数据模型 Step4PilotHandoffRefresh → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r102_production_step3_human_decision_session | 50KB | 3/0/5/0/3/0 | distill:gates-review |
| phase2r11101_step4_human_visual_review_strict_admission | 49KB | 1/0/0/0/2/0 | distill:gates-review |
| phase2r104_step3_explicit_production_authorization | 48KB | 1/0/0/0/3/0 | distill:gates-review；【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card + 运行证物三型(AcceptanceReport/OfflineGate/PriorFreeze/FrozenArtifact) → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r52_fagui_reviewed_fixture_audit | 46KB | 1/0/1/0/2/0 | distill:gates-review |
| phase2r38_step4_card2_reviewed_image_fixture | 41KB | 1/1/2/0/1/0 | distill:gates-review |
| phase2r53_fagui_reviewed_v2_evidence | 40KB | 1/0/4/0/2/0 | distill:gates-review |
| phase2r2242_step3_reviewed_release_merge | 39KB | 1/1/10/0/1/0 | distill:gates-review；【A案】跨桶数据模型 合并三型(Step3UnifiedReviewedReleaseFixture/Step3MergedReviewedReleaseCard/Step3MergedSourceOwnedDisplayText) → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r224_step3_final_review | 38KB | 1/1/2/0/1/0 | distill:gates-review |
| phase2r103_step3_production_raw_decision_ingestion | 36KB | 1/0/1/0/3/0 | distill:gates-review；【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py) |
| phase2r16_final_release_review | 34KB | 1/1/6/0/1/0 | distill:gates-review |
| phase2r54_fagui_typed_presentation_admission | 29KB | 1/0/1/0/2/0 | distill:gates-review |

## 4.4 提炼源：release gate（发布就绪）（4 个，117KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| phase2r15_release_readiness | 43KB | 1/1/1/0/1/0 | distill:release-gate |
| phase2r98_generic_step3_deterministic_release_closure | 37KB | 1/0/1/0/3/0 | distill:release-gate；【A案】跨桶数据模型 DeterministicDraftCard/DeterministicReleaseBundle → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py·r9/r10x 变体) |
| phase2r152_release_readiness_refresh | 20KB | 1/1/1/0/1/0 | distill:release-gate |
| phase2r6_generic_multi_subject_release_checkpoint | 17KB | 1/0/0/0/2/0 | distill:release-gate |

## 4.5 原地归档（pilot/smoke/freeze/checkpoint 等一次性）（58 个，2623KB）

| 模块 | 体量 | 引用(测试/导出/phase/核心/tools/其他) | 去向 |
|---|---|---|---|
| phase2r1110_generic_step4_source_backed_fresh_real_image_pilot | 89KB | 1/0/1/0/2/0 | archive |
| phase2r43_step4_card14_formula_fresh_smoke | 84KB | 1/0/0/0/2/0 | archive |
| phase2r56_generic_typed_evidence_batch | 81KB | 1/0/1/0/2/0 | archive |
| phase2r119_active_chatgpt2api_new_fresh_real_image_pilot | 80KB | 2/0/0/0/2/0 | archive |
| phase2r41_step4_generic_representative_pilot | 76KB | 1/0/1/0/2/0 | archive |
| phase2r117_generic_step4_source_backed_projection_real_pilot | 70KB | 2/0/1/0/2/0 | archive |
| phase2r72_cross_subject_typed_evidence_manual_pilot | 68KB | 2/0/2/0/2/0 | archive |
| phase2r115_generic_step4_provider_image_profile_fresh_real_pilot | 66KB | 1/0/0/0/2/0 | archive |
| phase2r57_fagui_typed_evidence_source_page_pilot | 66KB | 1/0/0/0/2/0 | archive |
| phase2r113_generic_step4_provider_image_profile_fresh_real_pilot | 65KB | 2/0/1/0/3/0 | archive |
| phase2r5_cross_domain_generic_skill | 62KB | 2/0/3/0/2/0 | archive |
| phase2r111_generic_step4_representative_real_image_pilot | 59KB | 1/0/1/0/3/0 | archive |
| phase2r318_step4_card4_constraint_live_smoke | 58KB | 1/1/1/0/2/0 | archive |
| phase2r91_generic_step3_representative_pilot | 54KB | 1/0/1/0/4/0 | archive |
| phase2r315_step4_card3_ordered_list_live_smoke | 54KB | 1/1/1/0/2/0 | archive |
| phase2r93_generic_step3_strict_response_representative_pilot | 53KB | 1/0/1/0/4/0 | archive |
| phase2r35_step4_card2_exact_label_live_smoke | 53KB | 1/1/6/0/2/0 | archive |
| phase2r146_v25b_canonical_live | 52KB | 10/1/9/0/1/0 | archive |
| phase2r31_step4_card2_live_smoke | 51KB | 10/1/9/0/11/0 | archive |
| phase2r33_step4_card2_b64_closure | 51KB | 1/1/2/0/3/0 | archive |
| phase2r312_step4_card6_table_border_live_smoke | 51KB | 1/1/1/0/1/0 | archive |
| phase2r39_step4_card6_complete_table_preflight | 50KB | 1/1/2/0/1/0 | archive |
| phase2r310_step4_card6_complete_table_live_smoke | 50KB | 1/1/1/0/1/0 | archive |
| phase2r37_step4_card2_connector_topology_live_smoke | 50KB | 1/1/1/0/2/0 | archive |
| phase2r223_step3_source_owned_live | 50KB | 4/1/4/0/1/0 | archive |
| phase2r55_fagui_typed_evidence_authoring | 49KB | 1/0/1/0/2/0 | archive |
| phase2r2234_step3_openai_strict | 49KB | 1/1/1/0/2/0 | archive |
| phase2r89_production_raw_draft_source_page_closure | 48KB | 2/0/1/0/3/0 | archive |
| phase2r3_step4_image_preflight | 47KB | 1/1/1/0/1/0 | archive |
| phase2r314_step4_card3_ordered_list_preflight | 47KB | 1/1/2/0/1/0 | archive |
| phase2r317_step4_card4_constraint_preflight | 46KB | 1/1/2/0/1/0 | archive |
| phase2r1413_gonglu62_cost_flow_preflight | 46KB | 2/1/1/0/1/0 | archive |
| phase2r1171b_historical_report_loss_reissuance | 43KB | 1/0/1/0/2/0 | archive |
| phase2r21_step3_live_smoke | 41KB | 1/1/5/0/1/0 | archive |
| phase2r22_step3_teaching_reconciliation | 40KB | 2/1/7/0/1/0 | archive |
| phase2r2233_step3_strict_recovery | 39KB | 1/1/1/0/1/0 | archive |
| phase2r7_existing_multi_subject_corpus_census | 38KB | 1/0/1/0/2/0 | archive |
| phase2r1414_gonglu62_cost_flow_live | 37KB | 1/1/0/0/1/0 | archive |
| phase2r1_five_golden | 37KB | 1/1/0/0/1/0 | archive |
| phase2r1171c_historical_legacy_checkpoint_fixture_isolation | 34KB | 3/0/1/0/2/0 | archive |
| phase2r2_step3_source_preflight | 34KB | 5/1/10/0/1/0 | archive |
| phase2r1412_gonglu55_standard_live | 33KB | 1/1/1/0/1/0 | archive |
| phase2r1410_gonglu01_text_list_live | 33KB | 1/1/1/0/1/0 | archive |
| phase2r148_gonglu41_diagram_live | 32KB | 1/1/4/0/1/0 | archive |
| phase2r1411_gonglu55_standard_preflight | 32KB | 2/1/1/0/1/0 | archive |
| phase2r149_gonglu01_text_list_preflight | 31KB | 2/1/1/0/1/0 | archive |
| phase2r1171_historical_r116_freeze_fixture_isolation | 31KB | 2/0/1/0/2/0 | archive |
| phase2r147_gonglu41_diagram_preflight | 29KB | 2/1/3/0/1/0 | archive |
| phase2r51_real_subject_onboarding | 28KB | 1/0/0/0/2/0 | archive |
| phase2r1191_historical_freeze_drift_loss_attestation | 27KB | 1/0/0/0/2/0 | archive |
| phase2r1192_historical_r1171c_fixture_isolation | 23KB | 2/0/0/0/2/0 | archive |
| phase2r143_v25_atomic_contract | 22KB | 6/1/7/0/1/0 | archive |
| phase2r225_step4_handoff | 21KB | 1/1/2/0/1/0 | archive |
| phase2r144_v25_atomic_live | 19KB | 1/1/2/0/2/0 | archive |
| phase2r5_synthetic_fixtures | 14KB | 2/0/1/0/1/0 | archive |
| phase2r145_v25_atomic_reissue | 12KB | 1/1/1/0/1/0 | archive |
| phase2r141_v25_recovery_live | 10KB | 1/0/0/0/1/0 | archive |
| phase2r1451_v25_teaching_layers | 8KB | 2/1/1/0/1/0 | archive |

## 五、evidence_v2 / materials_v2 —— 原样整包搬运

- `evidence_v2`：17 个文件，109KB → `src/examcard/evidence/`（只改 import 路径）
- `materials_v2`：3 个文件，9KB → `src/examcard/materials/`（只改 import 路径）

## 五-2、scripts/tools 构成

- 共 344 个 .py；其中 **289 个是 phase/v2 伴生工具**（reconcile_phase2r*/preflight_phase2r* 等）→ 全部留旧仓，与对应 phase 同进退；
- 其余 55 个为 V1 生产工具（full_check/reconcile/safe_writeback 等）→ 随 V1 生产留守旧仓；
- `export_v2_schemas.py` 不迁移：新仓改为自动发现 BaseModel 子类导出，替代手工注册。
- ⚠ tools/* 全部留旧仓，是 RC2 里大量 phase 伴生测试无法干净迁走的主因（见下）。

## 六、测试归属（RC2·import 闭包判定；扫描到与 v2 子系统相关的测试共 173 个）

### 随核心迁移（依赖闭包在新仓自洽，21 个）

判据：只引用迁走的 knowledge_v2 模块（core/current/current-runtime）或 evidence_v2/materials_v2，且**不**引用 tools/*、留仓 phase/historical、fact_check_lib/prompts/legacy。

- `tests/test_diagram_disposition_v2.py`（引用：diagram_disposition, fact_engine, schema）
- `tests/test_evidence_v2_engine.py`（引用：evidence_v2, materials_v2）
- `tests/test_fact_v2_engine.py`（引用：fact_engine, schema）
- `tests/test_learning_fixture_v2.py`（引用：learning_fixture, evidence_v2）
- `tests/test_phase25f_structural_reviews_v2.py`（引用：diagram_disposition, diagram_sidecar, fact_engine, schema）
- `tests/test_phase2r04_learning_table_closure.py`（引用：learning_fixture, schema, semantic_contracts, semantic_llm, semantic_projection …）
- `tests/test_phase2r054_table_atomic_continuation.py`（引用：learning_fixture, schema, semantic_contracts, semantic_llm, semantic_projection …）
- `tests/test_phase2r05_complete_table_semantics.py`（引用：schema, semantic_contracts, semantic_llm, semantic_projection, table_repair_sidecar …）
- `tests/test_phase2r06_multipage_continuation.py`（引用：schema, semantic_contracts, semantic_llm, semantic_projection, table_repair_sidecar …）
- `tests/test_phase2r07_real_continuation_golden.py`（引用：continuation_review, schema, semantic_contracts, semantic_llm, semantic_projection …）
- `tests/test_phase2r0981_composition_numeric_occurrences.py`（引用：schema, semantic_contracts, semantic_llm, semantic_projection, table_continuation_integration …）
- `tests/test_phase2r12_v25_golden_review.py`（引用：continuation_review, diagram_sidecar, learning_fixture, phase2r12_v25_golden_review）
- `tests/test_phase2r13_v25_reconciliation.py`（引用：learning_fixture, phase2r13_v25_reconciliation, table_repair_sidecar, evidence_v2）
- `tests/test_phase2r142_v25_projection.py`（引用：phase2r142_v25_projection, phase2r14_v25_live, schema, semantic_contracts, v25_generic_contracts）
- `tests/test_semantic_contracts_v2.py`（引用：schema, semantic_contracts, semantic_llm, semantic_projection, evidence_v2 …）
- `tests/test_semantic_llm_v2.py`（引用：schema, semantic_contracts, semantic_llm, semantic_projection, evidence_v2 …）
- `tests/test_semantic_projection_v2.py`（引用：semantic_contracts, semantic_projection, evidence_v2, materials_v2）
- `tests/test_teaching_quality_v2.py`（引用：diagram_sidecar, schema, semantic_contracts, teaching_quality, evidence_v2）
- `tests/test_v1_adapter_v2.py`（引用：v1_adapter）
- `tests/test_v2_contracts.py`（引用：continuation_review, diagram_sidecar, identity, learning_fixture, schema …）
- `tests/test_vision_idempotency.py`（引用：evidence_v2）

### 留旧仓（还依赖留仓物，随伴生 phase/tools 同进退，152 个）

以下测试虽然触及迁走代码，但仍依赖留旧仓的东西（tools/* 运行器、留仓 phase/historical 模块等），搬到新仓会 import 失败，故随其伴生物留旧仓：

| 测试 | 卡住原因(留仓依赖) |
|---|---|
| `conftest.py` | kv2:phase2r1171_historical_r116_freeze_fixture_isolation |
| `phase2r1110_historical_pytest_compat.py` | kv2:phase2r113_generic_step4_provider_image_profile_fresh_real_pilot |
| `phase2r1171c_historical_checkpoint_compat.py` | kv2:phase2r101_relay_baseline_compatibility,phase2r1171c_historical_legacy_checkpoint_fixture_isolation,phase2r96_generic_step3_provider_envelope_profile_remediation |
| `phase2r1191_historical_pytest_compat.py` | kv2:phase2r118_gpt_image2_http_502_source_backed_relay_remediation,phase2r119_active_chatgpt2api_new_fresh_real_image_pilot |
| `phase2r1192_historical_r1171c_fixture_compat.py` | kv2:phase2r1171c_historical_legacy_checkpoint_fixture_isolation,phase2r1192_historical_r1171c_fixture_isolation |
| `test_diagram_controlled_vision_v2.py` | pkg:tools |
| `test_diagram_sidecar_v2.py` | pkg:tools |
| `test_phase2r053_learning_page_completion.py` | pkg:tools |
| `test_phase2r081_v12_shape_reissue.py` | pkg:tools |
| `test_phase2r082_minimal_proposal_contract.py` | pkg:tools |
| `test_phase2r082_v13_live_runner.py` | pkg:tools |
| `test_phase2r083_numeric_expression_grounding.py` | pkg:tools |
| `test_phase2r083_v14_live_runner.py` | pkg:tools |
| `test_phase2r084_single_role_anchor_contract.py` | pkg:tools |
| `test_phase2r084_v15_live_runner.py` | pkg:tools |
| `test_phase2r085_deterministic_numeric_claims.py` | pkg:tools |
| `test_phase2r085_v16_live_runner.py` | pkg:tools |
| `test_phase2r086_canonical_title_projection.py` | pkg:tools |
| `test_phase2r087_step2b_bridge_card_golden.py` | pkg:tools |
| `test_phase2r089_cross_page_table_continuation.py` | pkg:tools |
| `test_phase2r08_v12_live_runner.py` | pkg:tools |
| `test_phase2r0910_v23_step2b_live_runner.py` | pkg:tools; kv2:v23_card_candidate_review,v23_step2b_bridge |
| `test_phase2r0911_v24_grouping_obligations.py` | pkg:tools; kv2:v23_card_candidate_review,v24_step2b_grouping_obligations |
| `test_phase2r0912_v24_step2b_live_runner.py` | pkg:tools; kv2:v23_card_candidate_review,v24_step2b_grouping_obligations |
| `test_phase2r0913_v24_card_candidate_review.py` | pkg:tools; kv2:v23_card_candidate_review,v24_card_candidate_review,v24_step2b_grouping_obligations |
| `test_phase2r093_coverage_obligations.py` | pkg:tools |
| `test_phase2r094_v18_live_runner.py` | pkg:tools |
| `test_phase2r095_coverage_obligation_context.py` | pkg:tools |
| `test_phase2r0961_v19_telemetry_learning_alignment.py` | pkg:tools; kv2:v19_learning_alignment |
| `test_phase2r096_v19_live_runner.py` | pkg:tools |
| `test_phase2r097_stiffness_atomic_threshold_coverage.py` | pkg:tools |
| `test_phase2r0982_v21_composition_occurrence_live.py` | pkg:tools |
| `test_phase2r0983_structural_index_occurrences.py` | pkg:tools |
| `test_phase2r0984_v22_structural_index_live.py` | pkg:tools |
| `test_phase2r0985_source_owned_structural_projection.py` | pkg:tools |
| `test_phase2r0985_v23_live_runner.py` | pkg:tools |
| `test_phase2r0987_v23_fact_card_reconciliation.py` | pkg:tools; kv2:v23_fact_card_reconciliation |
| `test_phase2r0988_v23_card_candidate_review.py` | pkg:tools; kv2:v23_card_candidate_review,v23_fact_card_reconciliation |
| `test_phase2r098_v20_live_runner.py` | pkg:tools |
| `test_phase2r099_v23_step2b_bridge.py` | pkg:tools; kv2:v23_card_candidate_review,v23_step2b_bridge |
| `test_phase2r09_table_continuation_request_integration.py` | pkg:tools |
| `test_phase2r101_relay_baseline_compatibility.py` | pkg:tools; kv2:phase2r101_relay_baseline_compatibility |
| `test_phase2r102_production_step3_human_decision_session.py` | pkg:tools; kv2:phase2r102_production_step3_human_decision_session,phase2r10_generic_step3_human_review_admission |
| `test_phase2r103_step3_production_raw_decision_ingestion.py` | pkg:tools; kv2:phase2r102_production_step3_human_decision_session,phase2r103_step3_production_raw_decision_ingestion |
| `test_phase2r104_step3_explicit_production_authorization.py` | pkg:tools; kv2:phase2r102_production_step3_human_decision_session,phase2r104_step3_explicit_production_authorization,phase2r10_generic_step3_human_review_admission |
| `test_phase2r10_generic_step3_human_review_admission.py` | pkg:tools; kv2:phase2r10_generic_step3_human_review_admission |
| `test_phase2r11101_step4_human_visual_review_strict_admission.py` | pkg:tools; kv2:phase2r11101_step4_human_visual_review_strict_admission |
| `test_phase2r1110_generic_step4_source_backed_fresh_real_image_pilot.py` | pkg:tools; kv2:phase2r1110_generic_step4_source_backed_fresh_real_image_pilot |
| `test_phase2r111_generic_step4_representative_real_image_pilot.py` | pkg:tools; kv2:phase2r111_generic_step4_representative_real_image_pilot |
| `test_phase2r112_generic_step4_provider_image_response_compatibility_remediation.py` | pkg:tools; kv2:phase2r112_generic_step4_provider_image_response_compatibility_remediation |
| `test_phase2r113_generic_step4_provider_image_profile_fresh_real_pilot.py` | pkg:tools; kv2:phase2r112_generic_step4_provider_image_response_compatibility_remediation,phase2r113_generic_step4_provider_image_profile_fresh_real_pilot |
| `test_phase2r114_active_gpt_image2_relay_capability_remediation.py` | pkg:tools; kv2:phase2r114_active_gpt_image2_relay_capability_remediation |
| `test_phase2r115_generic_step4_provider_image_profile_fresh_real_pilot.py` | pkg:tools; kv2:phase2r114_active_gpt_image2_relay_capability_remediation,phase2r115_generic_step4_provider_image_profile_fresh_real_pilot |
| `test_phase2r116_gpt_image2_http_422_request_compatibility_remediation.py` | pkg:tools; kv2:phase2r116_gpt_image2_http_422_request_compatibility_remediation |
| `test_phase2r1171_historical_r116_freeze_fixture_isolation.py` | pkg:tools; kv2:phase2r116_gpt_image2_http_422_request_compatibility_remediation,phase2r1171_historical_r116_freeze_fixture_isolation |
| `test_phase2r1171b_historical_report_loss_reissuance.py` | pkg:tools; kv2:phase2r1171b_historical_report_loss_reissuance,phase2r117_generic_step4_source_backed_projection_real_pilot |
| `test_phase2r1171c_historical_legacy_checkpoint_fixture_isolation.py` | pkg:tools; kv2:phase2r1171c_historical_legacy_checkpoint_fixture_isolation |
| `test_phase2r117_generic_step4_source_backed_projection_real_pilot.py` | pkg:tools; kv2:phase2r114_active_gpt_image2_relay_capability_remediation,phase2r117_generic_step4_source_backed_projection_real_pilot |
| `test_phase2r118_gpt_image2_http_502_source_backed_relay_remediation.py` | pkg:tools; kv2:phase2r118_gpt_image2_http_502_source_backed_relay_remediation |
| `test_phase2r1191_historical_freeze_drift_loss_attestation.py` | pkg:tools; kv2:phase2r1191_historical_freeze_drift_loss_attestation |
| `test_phase2r1192_historical_r1171c_fixture_isolation.py` | pkg:tools; kv2:phase2r1192_historical_r1171c_fixture_isolation |
| `test_phase2r119_active_chatgpt2api_new_fresh_real_image_pilot.py` | pkg:tools; kv2:phase2r119_active_chatgpt2api_new_fresh_real_image_pilot |
| `test_phase2r11_generic_contracts.py` | pkg:tools |
| `test_phase2r11_generic_step4_source_derived_image_compiler.py` | pkg:tools; kv2:phase2r11_generic_step4_source_derived_image_compiler |
| `test_phase2r1410_gonglu01_text_list_live.py` | kv2:phase2r1410_gonglu01_text_list_live,phase2r146_v25b_canonical_live,phase2r149_gonglu01_text_list_preflight |
| `test_phase2r1411_gonglu55_standard_preflight.py` | kv2:phase2r1411_gonglu55_standard_preflight,phase2r146_v25b_canonical_live |
| `test_phase2r1412_gonglu55_standard_live.py` | kv2:phase2r1411_gonglu55_standard_preflight,phase2r1412_gonglu55_standard_live,phase2r146_v25b_canonical_live |
| `test_phase2r1413_gonglu62_cost_flow_preflight.py` | kv2:phase2r1413_gonglu62_cost_flow_preflight,phase2r146_v25b_canonical_live |
| `test_phase2r1414_gonglu62_cost_flow_live.py` | kv2:phase2r1413_gonglu62_cost_flow_preflight,phase2r1414_gonglu62_cost_flow_live,phase2r146_v25b_canonical_live |
| `test_phase2r143_v25_atomic_contract.py` | kv2:phase2r143_v25_atomic_contract |
| `test_phase2r144_v25_atomic_live.py` | kv2:phase2r143_v25_atomic_contract,phase2r144_v25_atomic_live |
| `test_phase2r1451_v25_teaching_layers.py` | kv2:phase2r143_v25_atomic_contract,phase2r1451_v25_teaching_layers |
| `test_phase2r145_v25_atomic_reissue.py` | kv2:phase2r143_v25_atomic_contract,phase2r145_v25_atomic_reissue |
| `test_phase2r146_v25b_canonical_live.py` | kv2:phase2r143_v25_atomic_contract,phase2r1451_v25_teaching_layers,phase2r146_v25b_canonical_live |
| `test_phase2r147_gonglu41_diagram_preflight.py` | kv2:phase2r146_v25b_canonical_live,phase2r147_gonglu41_diagram_preflight |
| `test_phase2r148_gonglu41_diagram_live.py` | kv2:phase2r146_v25b_canonical_live,phase2r147_gonglu41_diagram_preflight,phase2r148_gonglu41_diagram_live |
| `test_phase2r149_gonglu01_text_list_preflight.py` | kv2:phase2r146_v25b_canonical_live,phase2r149_gonglu01_text_list_preflight |
| `test_phase2r14_v25_live_smoke.py` | kv2:phase2r141_v25_recovery_live |
| `test_phase2r151_gonglu24_remediation_live.py` | kv2:phase2r143_v25_atomic_contract,phase2r146_v25b_canonical_live,phase2r151_gonglu24_remediation_live |
| `test_phase2r152_release_readiness_refresh.py` | kv2:phase2r152_release_readiness_refresh |
| `test_phase2r15_release_readiness.py` | kv2:phase2r15_release_readiness |
| `test_phase2r16_final_release_review.py` | kv2:phase2r16_final_release_review |
| `test_phase2r1_five_golden_offline.py` | pkg:tools; kv2:phase2r1_five_golden |
| `test_phase2r21_step3_live_smoke.py` | kv2:phase2r21_step3_live_smoke,phase2r2_step3_source_preflight |
| `test_phase2r2231_step3_relay_recovery.py` | kv2:phase2r2231_step3_relay_recovery,phase2r223_step3_source_owned_live,phase2r22_step3_teaching_reconciliation |
| `test_phase2r2232_step3_relay_diagnostic.py` | kv2:phase2r2232_step3_relay_diagnostic |
| `test_phase2r2233_step3_strict_recovery.py` | kv2:phase2r2233_step3_strict_recovery,phase2r223_step3_source_owned_live |
| `test_phase2r2234_step3_openai_strict.py` | kv2:phase2r2234_step3_openai_strict,phase2r223_step3_source_owned_live,phase2r2_step3_source_preflight |
| `test_phase2r223_step3_source_owned_live.py` | kv2:phase2r223_step3_source_owned_live |
| `test_phase2r2241_step3_source_text_remediation.py` | kv2:phase2r2241_step3_source_text_remediation |
| `test_phase2r2242_step3_reviewed_release_merge.py` | kv2:phase2r2242_step3_reviewed_release_merge,phase2r2_step3_source_preflight |
| `test_phase2r224_step3_final_review.py` | kv2:phase2r224_step3_final_review |
| `test_phase2r225_step4_handoff.py` | kv2:phase2r225_step4_handoff |
| `test_phase2r22_step3_teaching_reconciliation.py` | kv2:phase2r22_step3_teaching_reconciliation,phase2r2_step3_source_preflight |
| `test_phase2r2_step3_source_preflight.py` | kv2:phase2r2_step3_source_preflight |
| `test_phase2r310_step4_card6_complete_table_live_smoke.py` | kv2:phase2r310_step4_card6_complete_table_live_smoke,phase2r31_step4_card2_live_smoke |
| `test_phase2r311_step4_card6_table_border_remediation.py` | kv2:phase2r311_step4_card6_table_border_remediation |
| `test_phase2r312_step4_card6_table_border_live_smoke.py` | kv2:phase2r312_step4_card6_table_border_live_smoke,phase2r31_step4_card2_live_smoke |
| `test_phase2r313_step4_card6_reviewed_image_fixture.py` | kv2:phase2r313_step4_card6_reviewed_image_fixture |
| `test_phase2r314_step4_card3_ordered_list_preflight.py` | kv2:phase2r314_step4_card3_ordered_list_preflight |
| `test_phase2r315_step4_card3_ordered_list_live_smoke.py` | kv2:phase2r315_step4_card3_ordered_list_live_smoke,phase2r31_step4_card2_live_smoke |
| `test_phase2r316_step4_card3_reviewed_image_fixture.py` | kv2:phase2r316_step4_card3_reviewed_image_fixture |
| `test_phase2r317_step4_card4_constraint_preflight.py` | kv2:phase2r317_step4_card4_constraint_preflight |
| `test_phase2r318_step4_card4_constraint_live_smoke.py` | kv2:phase2r318_step4_card4_constraint_live_smoke,phase2r31_step4_card2_live_smoke |
| `test_phase2r319_step4_card4_reviewed_image_fixture.py` | kv2:phase2r319_step4_card4_reviewed_image_fixture |
| `test_phase2r31_step4_card2_live_smoke.py` | kv2:phase2r31_step4_card2_live_smoke |
| `test_phase2r32_step4_image_relay_recovery.py` | kv2:phase2r31_step4_card2_live_smoke,phase2r32_step4_image_relay_recovery |
| `test_phase2r33_step4_card2_b64_closure.py` | kv2:phase2r101_relay_baseline_compatibility,phase2r31_step4_card2_live_smoke,phase2r33_step4_card2_b64_closure |
| `test_phase2r34_step4_card2_exact_label_remediation.py` | kv2:phase2r31_step4_card2_live_smoke,phase2r34_step4_card2_exact_label_remediation |
| `test_phase2r35_step4_card2_exact_label_live_smoke.py` | kv2:phase2r31_step4_card2_live_smoke,phase2r35_step4_card2_exact_label_live_smoke |
| `test_phase2r36_step4_card2_connector_topology_remediation.py` | kv2:phase2r36_step4_card2_connector_topology_remediation |
| `test_phase2r37_step4_card2_connector_topology_live_smoke.py` | kv2:phase2r31_step4_card2_live_smoke,phase2r37_step4_card2_connector_topology_live_smoke |
| `test_phase2r38_step4_card2_reviewed_image_fixture.py` | kv2:phase2r38_step4_card2_reviewed_image_fixture |
| `test_phase2r39_step4_card6_complete_table_preflight.py` | kv2:phase2r39_step4_card6_complete_table_preflight |
| `test_phase2r3_step4_image_preflight.py` | kv2:phase2r3_step4_image_preflight |
| `test_phase2r41_step4_generic_representative_pilot.py` | pkg:tools; kv2:phase2r41_step4_generic_representative_pilot |
| `test_phase2r42_step4_card14_formula_remediation.py` | pkg:tools; kv2:phase2r42_step4_card14_formula_remediation |
| `test_phase2r43_step4_card14_formula_fresh_smoke.py` | pkg:tools; kv2:phase2r43_step4_card14_formula_fresh_smoke |
| `test_phase2r4_step4_generic_contract_compiler.py` | pkg:tools; kv2:phase2r4_step4_generic_contract_compiler |
| `test_phase2r51_real_subject_onboarding.py` | pkg:tools; kv2:phase2r51_real_subject_onboarding,phase2r5_cross_domain_generic_skill,phase2r5_synthetic_fixtures |
| `test_phase2r52_fagui_reviewed_fixture_audit.py` | pkg:tools; kv2:phase2r52_fagui_reviewed_fixture_audit |
| `test_phase2r53_fagui_reviewed_v2_evidence.py` | pkg:tools; kv2:phase2r53_fagui_reviewed_v2_evidence |
| `test_phase2r54_fagui_typed_presentation_admission.py` | pkg:tools; kv2:phase2r54_fagui_typed_presentation_admission |
| `test_phase2r55_fagui_typed_evidence_authoring.py` | pkg:tools; kv2:phase2r55_fagui_typed_evidence_authoring |
| `test_phase2r56_generic_typed_evidence_batch.py` | pkg:tools; kv2:phase2r56_generic_typed_evidence_batch |
| `test_phase2r57_fagui_typed_evidence_source_page_pilot.py` | pkg:tools; kv2:phase2r57_fagui_typed_evidence_source_page_pilot |
| `test_phase2r5_cross_domain_generic_skill.py` | pkg:tools; kv2:phase2r5_cross_domain_generic_skill,phase2r5_synthetic_fixtures |
| `test_phase2r6_generic_multi_subject_release_checkpoint.py` | pkg:tools; kv2:phase2r6_generic_multi_subject_release_checkpoint |
| `test_phase2r71_multi_subject_source_confirmed_evidence_admission.py` | pkg:tools; kv2:phase2r71_multi_subject_source_confirmed_evidence_admission |
| `test_phase2r72_cross_subject_typed_evidence_manual_pilot.py` | pkg:tools; kv2:phase2r72_cross_subject_typed_evidence_manual_pilot |
| `test_phase2r73_generic_typed_evidence_review_queue.py` | pkg:tools; kv2:phase2r72_cross_subject_typed_evidence_manual_pilot,phase2r73_generic_typed_evidence_review_queue |
| `test_phase2r7_existing_multi_subject_corpus_census.py` | pkg:tools; kv2:phase2r7_existing_multi_subject_corpus_census |
| `test_phase2r810_r89_reviewed_admission_authorization.py` | pkg:tools; kv2:phase2r810_r89_reviewed_admission_authorization,phase2r89_production_raw_draft_source_page_closure |
| `test_phase2r81_multi_subject_typed_evidence_admission.py` | pkg:tools; kv2:phase2r81_multi_subject_typed_evidence_admission |
| `test_phase2r82_generic_review_decision_ingestion.py` | pkg:tools; kv2:phase2r82_generic_review_decision_ingestion |
| `test_phase2r83_generic_typed_evidence_review_workbench.py` | pkg:tools; kv2:phase2r83_generic_typed_evidence_review_workbench |
| `test_phase2r84_selected_batch_human_decision_ingestion.py` | pkg:tools; kv2:phase2r84_selected_batch_human_decision_ingestion |
| `test_phase2r85_production_human_source_page_decision_workbench.py` | pkg:tools; kv2:phase2r84_selected_batch_human_decision_ingestion,phase2r85_production_human_source_page_decision_workbench |
| `test_phase2r86_r85_production_human_decision_explicit_export.py` | pkg:tools; kv2:phase2r84_selected_batch_human_decision_ingestion,phase2r85_production_human_source_page_decision_workbench,phase2r86_r85_production_human_decision_explicit_export |
| `test_phase2r87_production_source_page_human_decision_workbench.py` | pkg:tools; kv2:phase2r84_selected_batch_human_decision_ingestion,phase2r85_production_human_source_page_decision_workbench,phase2r86_r85_production_human_decision_explicit_export,phase2r87_production_source_page_human_decision_workbench |
| `test_phase2r89_production_raw_draft_source_page_closure.py` | pkg:tools; kv2:phase2r84_selected_batch_human_decision_ingestion,phase2r89_production_raw_draft_source_page_closure |
| `test_phase2r8_one_subject_typed_evidence_admission.py` | pkg:tools; kv2:phase2r8_one_subject_typed_evidence_admission |
| `test_phase2r91_generic_step3_representative_pilot.py` | pkg:tools; kv2:phase2r91_generic_step3_representative_pilot |
| `test_phase2r92_generic_step3_response_contract.py` | pkg:tools; kv2:phase2r92_generic_step3_response_contract |
| `test_phase2r93_generic_step3_strict_response_representative_pilot.py` | pkg:tools; kv2:phase2r92_generic_step3_response_contract,phase2r93_generic_step3_strict_response_representative_pilot |
| `test_phase2r94_generic_step3_provider_envelope_compatibility_remediation.py` | pkg:tools; kv2:phase2r94_generic_step3_provider_envelope_compatibility_remediation |
| `test_phase2r95_generic_step3_provider_envelope_real_pilot.py` | pkg:tools; kv2:phase2r94_generic_step3_provider_envelope_compatibility_remediation,phase2r95_generic_step3_provider_envelope_real_pilot |
| `test_phase2r96_generic_step3_provider_envelope_profile_remediation.py` | pkg:tools; kv2:phase2r96_generic_step3_provider_envelope_profile_remediation |
| `test_phase2r97_generic_step3_provider_envelope_profile_real_pilot.py` | pkg:tools; kv2:phase2r96_generic_step3_provider_envelope_profile_remediation,phase2r97_generic_step3_provider_envelope_profile_real_pilot |
| `test_phase2r98_generic_step3_deterministic_release_closure.py` | pkg:tools; kv2:phase2r98_generic_step3_deterministic_release_closure |
| `test_phase2r9_generic_step3_card_compiler.py` | pkg:tools; kv2:phase2r9_generic_step3_card_compiler |
| `test_v2_shadow_run.py` | pkg:tools |

## 七、修订说明与复核留痕（v2/v3）

### RC1 · archive 安全性修正
- 迁走集 import 闭包自动上提的 phase 模块（5 个）：`phase2r11_generic_five_golden`, `phase2r12_v25_golden_review`, `phase2r13_v25_reconciliation`, `phase2r142_v25_projection`, `phase2r14_v25_live`。
- 触发边：`v25_teaching_gate_layers`（现役契约）第 8 行 `from .phase2r142_v25_projection import V25GoldenTargetState, V25ResponseGoldenProjectionBundle`。
- 旧版把这些判为 archive/留旧仓 → 新仓 `contracts` 包会 import 断。v2 归入 `current-runtime` 随现役契约物理迁走。

### RC2 · 测试归属修正
- 相关测试 173 个：随核心迁移 **21**、留旧仓 **152**。
- 旧版规则“引用任一 core 即迁走”把大量只借类型的 phase 伴生测试误判为迁走（其真实依赖 tools/*、phase、historical 全部留旧仓，开箱即挂）。v2 改为 import 闭包判定，并纳入 evidence_v2/materials_v2。

### RC3 · 两处路由修正 + 扫描盲区（n4 施工实测回填，2026-08-12）
- 路由修正 ①：`learning_fixture` 由 v2 的 `tests/golden/` 改落 `src/examcard/knowledge/learning_fixture.py`。
  实测其被 V25 运行时闭包五模块**顶层 import**（`phase2r11_generic_five_golden` / `phase2r12_v25_golden_review` / `phase2r13_v25_reconciliation` / `phase2r14_v25_live` / `phase2r142_v25_projection`，均迁 `knowledge/contracts/`）；若落 tests/（不随包安装、不在包内），新仓 contracts 包 import 即断。
- 路由修正 ②：`step2b_card_golden` 由 v2 的 `tests/golden/` 改落 `src/examcard/knowledge/step2b_card_golden.py`。
  实测其被 `table_continuation`（新仓 `continuation/core.py`）**函数内缩进式惰性 import**（旧仓 `table_continuation.py` 第 525 行，原地注释：为避免模块级循环依赖而局部导入），同属运行时闭包。
- 扫描盲区留痕（供后续清点/复核者防坑）：
  - **缩进式惰性 import**（函数体内 `from .x import y`）：行首锚定的正则会漏检。本生成器 `scan_kv2_imports` 的正则未锚定行首、实际可扫到（step2b_card_golden 的引用计数即含此边）；v2 误路由的根因是 `CORE_TARGETS` 手工路由草案未消费该闭包信号，v3 已修正路由。
  - **动态字符串导入**（`__import__("knowledge_v2.x")` / `importlib.import_module("knowledge_v2.x")`）：一切 from/import 文本正则均扫不到。实例：`phase2r14_v25_live` 第 336 行 `__import__("knowledge_v2.v25_generic_contracts", fromlist=[…])`；因该模块第 55 行另有同目标的顶层静态 import，本例未造成闭包漏边。v3 生成器已补字符串字面量检测兜底（`_imp_dyn`）。
- 兜底检测的实际影响（v2 → v3 复扫对比）：**模块分类与迁走/留仓集合零变化**（RC1 上提五模块不变）；新浮出 2 个此前完全不可见的『留旧仓』测试——`phase2r1110_historical_pytest_compat.py`、`phase2r1191_historical_pytest_compat.py`（均经 `importlib.import_module` 动态引用留仓 phase 模块），相关测试总数 171 → 173（迁走 21 不变，留旧仓 150 → 152）。

### A 案 · 跨桶数据模型下沉注解（总指挥指派，2026-08-11）
- 依据 `docs/design/distill_gates_review_design.md` §4「当前生产形态清单」/§5 契约 2·6：以下 8 个 distill 模块的**跨桶消费数据模型**下沉 `knowledge/contracts/admission_fixtures.py`，法定哈希搬运件 `admission_legacy_hash.py` 随之下沉（铁律 bc-b8b9aca6：逐字节原样、禁入 common/hashing）；其判定/编译逻辑仍归各自桶。
  - `phase2r103_step3_production_raw_decision_ingestion`（distill:gates-review）【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
  - `phase2r104_step3_explicit_production_authorization`（distill:gates-review）【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card + 运行证物三型(AcceptanceReport/OfflineGate/PriorFreeze/FrozenArtifact) → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
  - `phase2r10_generic_step3_human_review_admission`（distill:gates-review）【A案】跨桶数据模型 ReviewedStep3Fixture/ReviewedStep3Card → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
  - `phase2r2242_step3_reviewed_release_merge`（distill:gates-review）【A案】跨桶数据模型 合并三型(Step3UnifiedReviewedReleaseFixture/Step3MergedReviewedReleaseCard/Step3MergedSourceOwnedDisplayText) → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
  - `phase2r319_step4_card4_reviewed_image_fixture`（distill:gates-review）【A案】跨桶数据模型 Step4PilotHandoffRefresh → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
  - `phase2r810_r89_reviewed_admission_authorization`（distill:gates-review）【A案】跨桶数据模型 ReviewedTypedEvidenceFixture → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
  - `phase2r98_generic_step3_deterministic_release_closure`（distill:release-gate）【A案】跨桶数据模型 DeterministicDraftCard/DeterministicReleaseBundle → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py·r9/r10x 变体)
  - `phase2r9_generic_step3_card_compiler`（distill:compilers）【A案】跨桶数据模型 Step3CardContract → knowledge/contracts/admission_fixtures.py(+admission_legacy_hash.py)
- 该注解只补『去向』列语义，不改任何模块的迁走/留仓归类，故自检两条不变式不受影响。

### 自检
- `migration/verify_manifest_audit.py` / `verify_manifest_audit2.py` 复用本文件的 `build_classification`，断言：①无迁走模块 import 留仓模块（archive 安全）；②每个『随核心迁移』测试的依赖闭包在新仓自洽。
