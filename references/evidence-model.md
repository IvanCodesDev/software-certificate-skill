# 证据图谱模型

## 目标

任何功能、步骤、截图、代码片段和申请表描述都能回溯到真实项目事实。图谱采用稳定标识，避免文件重命名后全部失联。

## 节点类型

| 类型 | 标识示例 | 最低字段 |
|---|---|---|
| 软件事实 | `FACT-version` | value, source, confirmed_by, confirmed_at |
| 源文件 | `FILE-a1b2c3` | path, sha256, language, line_count |
| 功能 | `CAP-outpatient-register` | name, actor, entry, outcome |
| 界面 | `UI-register-form` | route, title, source_files |
| 接口 | `API-register-submit` | method, path, handler |
| 数据对象 | `DATA-patient` | name, source_files, key_fields |
| 操作步骤 | `STEP-register-save` | action, expected_feedback |
| 截图 | `SHOT-register-result` | path, sha256, width, height, captured_at |
| 声明 | `CLAIM-main-function-01` | text, evidence_ids, status |

## 边类型

- `implements`：源文件实现功能。
- `opens`：功能打开界面。
- `calls`：界面调用接口。
- `reads` / `writes`：功能读写数据对象。
- `proves`：截图或代码证明声明。
- `precedes`：步骤顺序。
- `derived_from`：文档内容从事实或证据派生。

## 证据强度

- `direct`：运行界面、实际响应、测试结果、源代码中的明确处理逻辑。
- `corroborating`：路由、菜单配置、数据库对象、日志等旁证。
- `weak`：目录名、注释、README 中未经运行验证的描述。

关键功能至少包含一个 `direct` 证据，或两个相互独立的 `corroborating` 证据。`weak` 证据只生成调查线索。

## 声明生命周期

`candidate → evidenced → human_confirmed → published`

只有 `human_confirmed` 的声明进入正式手册；`candidate` 和 `evidenced` 可进入内部工作底稿。任何更改都记录来源和时间。

## 一致性键

以下键必须从 `application-facts.json` 单一派生：

`software_full_name`, `software_short_name`, `version`, `rightsholder`, `completion_date`, `publication_status`, `development_mode`, `rights_scope`。

所有文件记录 `facts_sha256`。事实变更后，旧产物自动标记为过期。
