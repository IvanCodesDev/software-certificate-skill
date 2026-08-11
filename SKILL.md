---
name: software-certificate-skill
description: 从真实软件项目建立可追溯证据图谱，自动运行网页并批量采集经过质量检查的界面截图，策划约60页的中文操作手册，制作带真实Word目录和规范黑白灰申报版式的DOCX，编排源程序识别材料，并校验申请表、代码、截图、功能与版本的一致性。适配 Codex、Claude Code、Cursor、OpenCode、WorkBuddy、QoderWork、TraeWork 及支持 Agent Skills 或 AGENTS.md 的平台。用户提到软件著作权、软著、自动截图、源程序材料、操作手册、用户手册、设计说明书、申请材料排版、60页手册或软著材料审查时使用。
---

# 软件著作权材料工作室

## 核心定位

把软著材料当作一个“可追溯出版项目”，而不是套话填充任务。先从项目中提取证据，再按页组织叙事，最后生成申报版并通过结构、视觉和一致性验收。

默认产物语言为简体中文，纸张为 A4。信息缺失时使用显式槽位（如 `【待申请人确认：首次发表日期】`），并把槽位写入问题清单。

## 平台与路径约定

先把当前文件所在目录解析为 `SKILL_ROOT`，再从 `SKILL_ROOT` 调用 `scripts/`、`references/` 和 `assets/`。不要假设 Skill 安装在某个固定产品目录，也不要把案例输出写进 Skill 安装目录。

Codex、Claude Code、Cursor、OpenCode 使用原生或通用 Skill 入口；WorkBuddy、QoderWork、TraeWork 使用通用 `.agents/skills`、`AGENTS.md` 和项目规则适配。安装或迁移时读取 [references/agent-platforms.md](references/agent-platforms.md)，运行 `scripts/install_agent_skill.py`，不要为每个平台复制一份不同的业务流程。

## 首次使用先读

1. 读取 [references/rules-2026.md](references/rules-2026.md)，确认规则快照日期和证据等级。
2. 读取 [references/evidence-model.md](references/evidence-model.md)，按统一标识组织事实。
3. 创建或改版手册时读取 [references/manual-architecture.md](references/manual-architecture.md) 与 [references/visual-system.md](references/visual-system.md)。
4. 交付前读取 [references/quality-gates.md](references/quality-gates.md)。
5. 用户要求“参考过审材料”时同时读取 [references/research-evidence-2026.md](references/research-evidence-2026.md)，区分公开可核验样本与经验性信息。
6. 创建操作手册或代码材料时读取 [references/layout-benchmarks.md](references/layout-benchmarks.md)，沿用已测量的黑白灰版式参数。
7. 选择源程序材料时读取 [references/source-selection.md](references/source-selection.md)，按完整源文件和真实业务相关性建立有序代码语料库。
8. 项目包含 Web 界面或需要批量截图时读取 [references/screenshot-automation.md](references/screenshot-automation.md)，先验证截图计划，再运行真实采集。
9. 在不同 Agent 平台安装或分发时读取 [references/agent-platforms.md](references/agent-platforms.md)，使用安装器生成原生入口或通用规则。

## 工作室循环

### 1. 建立规则快照

记录查询日期、官方页面、申请系统口径、适用登记类型和冲突项。A 级规则决定交付门槛；B 级流程决定当前操作；C 级资料只提供可观察的版式或表达启发。

若当前申请表含作者承诺、独立开发、AI 使用或真实性声明，先建立 `authorship_attestation`。提交材料必须与实际开发和材料形成过程相符，AI 参与事实保留在工作底稿中；涉及无 AI 声明时，提交版进入申请人独立撰写、逐页复核和签署流程。

### 2. 建立项目指纹与证据图谱

运行：

```powershell
python scripts/init_case.py --project PROJECT_ROOT --case CASE_DIR
python scripts/scan_project.py --project PROJECT_ROOT --output CASE_DIR/02-evidence/evidence-graph.json
```

每个功能声明至少绑定一种真实证据：路由、控制器、页面、命令、数据实体、接口响应、配置、测试、日志或截图。功能名、入口、角色、前置条件、正常结果和异常结果分别记录，避免从目录名直接推断业务结论。

### 2.5 自动采集界面证据

从 `assets/examples/screenshot-plan.example.json` 建立案例截图计划，固定浏览器视口、语言、时区、颜色模式、角色、路由、页面就绪条件和隐私遮罩。账号与密码从环境变量或外部浏览器状态读取，不写入仓库。

先验证，再采集并派生带截图节点的新证据图谱：

```powershell
python scripts/capture_web_screenshots.py `
  --plan CASE_DIR/02-evidence/screenshot-plan.json --validate-only
python scripts/capture_web_screenshots.py `
  --plan CASE_DIR/02-evidence/screenshot-plan.json `
  --output CASE_DIR/02-evidence/screenshots `
  --evidence-source CASE_DIR/02-evidence/evidence-graph.json `
  --evidence-output CASE_DIR/02-evidence/evidence-graph.with-screenshots.json `
  --fail-fast
```

每张截图必须有稳定 ID、功能图题、角色、路由、动作、截图前断言和证据关联。脚本等待服务健康、DOM、字体、图片、页面就绪选择器和操作结果，关闭动画与光标闪烁，记录控制台错误、页面异常、失败请求、SHA-256、尺寸、熵、内容比例和 dHash。空白图、尺寸不足图和非预期近重复图不进入正式手册。

### 3. 锁定申请事实

填写 `01-intake/application-facts.json`。软件全称、简称、版本、著作权人、完成日期、发表状态、开发方式、权利范围属于冻结字段。发现材料间冲突时更新事实文件，再统一派生，避免在多个文档中逐处手改。

### 4. 设计页级故事板

运行：

```powershell
python scripts/plan_manual.py --facts CASE_DIR/01-intake/application-facts.json `
  --evidence CASE_DIR/02-evidence/evidence-graph.json `
  --output CASE_DIR/03-storyboard/manual-plan.json --target-pages auto
python scripts/seed_manual.py --facts CASE_DIR/01-intake/application-facts.json `
  --plan CASE_DIR/03-storyboard/manual-plan.json `
  --output CASE_DIR/04-content/manual.json
```

完整操作手册通常控制在 40 页以上，复杂项目约 60 页。`auto` 模式依据可确认功能数量在 40–66 页之间分配故事板；只有真实内容确有需要时才扩展到 72 页。它是成册策略，并非通用法定最低页数。每页必须有不同的信息任务和证据标识；证据不足的页记为 `evidence_debt`，在生成提交版前补证。空白页、重复截图、同义扩写和无项目依据的常识段落不计入有效页数。

页级组织采用“任务闭环”：场景 → 入口 → 前置条件 → 操作 → 系统反馈 → 异常处理 → 结果核验。按角色与工作流编排，避免照菜单逐条复述。

### 5. 使用标准申报版式成册

统一使用 `standard-filing-gray.json`。该版式面向软件著作权申报材料，采用白底黑字、黑体标题、宋体正文、浅灰表头和黑灰细线，不使用品牌色、渐变、色块封面、英文装饰眉题或产品宣传式卡片。

在 `04-content/manual.json` 的页级骨架中填入经确认内容，再运行：

```powershell
python scripts/build_manual.py --input CASE_DIR/04-content/manual.json `
  --theme assets/themes/standard-filing-gray.json `
  --output CASE_DIR/06-output/操作手册-完整成册版.docx
powershell -ExecutionPolicy Bypass -File scripts/refresh_word_fields.ps1 `
  -Path CASE_DIR/06-output/操作手册-完整成册版.docx
```

目录使用 Word `TOC` 域、标题大纲级别和超链接开关；仅输入“目录文字 + 页码”的静态表格不算目录。截图保持原比例，不裁掉关键控件，截图下方给出编号和功能名称。证据标识保留在结构化底稿与质检报告中，不显示在正式操作手册页面。

### 6. 编排源程序识别材料

先人工确认源文件清单、开源依赖边界、生成代码边界与商业秘密处理方式。代码以完整源文件为单位纳入，优先覆盖申请软件自身的入口、控制、业务服务、领域对象和原创算法；排除第三方库、自动生成代码、缓存、密钥和无关模块。不得从多个文件中拼接零散函数来制造连续性。确认 `ordered_files` 与逐文件选择理由后再运行：

```powershell
python scripts/compose_code.py --project PROJECT_ROOT `
  --manifest CASE_DIR/05-source/source-manifest.json `
  --output-dir CASE_DIR/06-output/source
```

脚本保留逐行来源映射，生成完整归档版、申报 TXT、申报 DOCX 和 provenance JSON。程序总量达到 60 页时取前、后各连续 30 页；不足 60 页时提交全部。默认按每页 50 行排版，最后一页可少于 50 行。

### 7. 生成申报视图

完整成册版用于质量审阅和留档。若文档超过 60 页，根据识别材料规则另派生前、后各连续 30 页的申报视图；总量不足 60 页时使用全文。页码、标题、截图和上下文在裁切点保持连续可读，并在底稿中记录完整版本哈希与裁切映射。

### 8. 渲染验收与发布

使用 documents 技能的 `render_docx.py` 或 Word/PDF 工具渲染全部页面，逐页检查。随后运行：

```powershell
python scripts/verify_package.py --case CASE_DIR `
  --manual CASE_DIR/06-output/操作手册-完整成册版.docx `
  --screenshot-index CASE_DIR/02-evidence/screenshots/screenshot-index.json `
  --require-screenshots `
  --report CASE_DIR/07-qa/verification.json
```

Web 项目使用截图硬门禁；桌面端、移动端或命令行项目省略 `--require-screenshots`，并提供相应平台的真实运行截图索引和人工验收记录。

只有以下条件全部满足才形成发布包：真实 TOC 域存在、目录已刷新并可跳转、无断链标题、申请事实一致、所有关键功能有证据、自动截图索引完整、截图断言和质量检查通过、截图比例正确、无裁切溢出、无意外空白页、文档行密度与源程序行密度合规、申报裁切连续、所有待确认槽位已清零或被明确签署。

## 输出目录约定

```text
CASE_DIR/
  01-intake/       申请事实、作者声明、问题清单
  02-evidence/     项目指纹、证据图谱、截图索引
  03-storyboard/   页级故事板、页面预算、证据欠账
  04-content/      结构化手册正文
  05-source/       源文件清单、开源与保密边界
  06-output/       完整成册版、申报视图、源程序材料
  07-qa/           渲染指标、一致性报告、人工复核记录
  08-release/      最终发布包及校验和
```

## 编辑既有材料

先计算原件哈希并复制到 `CASE_DIR/00-original/`，编辑副本；输出变更补丁、精确验证命令与回滚脚本。复用已有证据、截图、页级审计和已通过结果，只处理未完成的变更点。

## 禁止性质量模式

- 虚构菜单、按钮、接口、角色、数据或成功结果。
- 为凑页数重复截图、重复定义或堆叠空泛优势。
- 用代码目录名代替业务功能证据。
- 把历史指南、代理机构文章或案例宣传当作现行官方规则。
- 静态假目录、手写页码、目录项与标题不一致。
- 对截图拉伸、裁掉导航或隐藏能证明真实性的系统状态。
- 软件名称、版本、著作权人、日期在表单、手册、代码页眉之间漂移。

## 交付摘要

最终回复列出：规则快照日期、采用的标准申报版式、完整手册页数、申报视图页数、源程序页数、证据覆盖率、目录跳转结果、关键一致性结果、待申请人签署项，以及全部绝对路径。
