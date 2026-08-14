# 网页自动截图与证据采集

## 目录

1. 目标
2. 采集前准备
3. 截图计划
4. 登录与状态复用
5. 稳定采集
6. 质量检查
7. Computer Use 收据
8. 状态与发布语义
9. 证据图谱回写
10. 故障处理

## 1. 目标

把截图当作可重复生成的项目证据，而不是人工随手截屏。每张图必须能回答：由哪个角色进入哪个路由、执行了什么动作、页面给出什么结果、关联哪个功能证据、图片文件是否通过质量检查。

优先运行本地或测试环境。固定窗口、缩放、语言、时区、颜色模式和测试数据；使用专用测试账号。正式材料只纳入能够复现且经过申请人核验的页面。

## 2. 采集前准备

安装可选浏览器依赖：

```powershell
python -m pip install "playwright>=1.49,<2"
python -m playwright install chromium
```

如果机器已经安装 Chrome 或 Edge，可在计划的 `browser.channel` 中使用 `chrome` 或 `msedge`，减少浏览器下载。

从 `assets/examples/screenshot-plan.example.json` 复制截图计划。先运行：

```powershell
python scripts/capture_web_screenshots.py --plan CASE_DIR/02-evidence/screenshot-plan.json --validate-only
```

## 3. 截图计划

每个 `capture` 至少填写：

- `id`：稳定、唯一、可被手册引用的标识；
- `title`：与手册图题一致的功能名称；
- `route` 或 `url`：真实页面入口；
- `role`：执行该操作的真实角色；
- `evidence_ids`：关联的功能、路由或代码证据；
- `ready_selector`：页面真正可用的判断条件；
- `actions`：为达到目标状态所需的最小操作；
- `assertions`：截图前必须成立的界面事实；
- `mask`：需要以灰块遮盖的个人数据或敏感字段。

选择稳定定位器，优先级为 `data-testid`、语义角色/标签、稳定业务属性，最后才使用易变的 CSS 层级或动态类名。

## 4. 登录与状态复用

登录有三种方式：

1. 在 `setup` 中执行登录动作，并用 `save_storage_state` 保存会话；
2. 使用已有 `storage_state`，适合无交互批量采集；
3. 使用 `user_data_dir` 复用持久浏览器配置，适合必须人工完成验证码或单点登录的场景。

账号、密码和令牌通过环境变量或外部状态文件提供，不写入仓库。示例中的 `${SCREENSHOT_USER}` 等槽位应在执行前由运行环境替换。状态文件放在案例目录并排除公开提交。

## 5. 稳定采集

脚本执行以下稳定化动作：

- 等待服务健康检查、DOM 就绪、可选网络空闲和 `ready_selector`；
- 等待字体与图片加载；
- 关闭动画、过渡、光标闪烁和平滑滚动；
- 记录控制台错误、页面异常和失败请求；
- 支持点击、填写、按键、选择、勾选、悬停、滚动、等待和断言；
- 支持视口截图、整页截图和指定元素截图；
- 支持临时隐藏开发浮层、Toast 等非业务遮挡；
- 失败时自动保存 `.failed.png` 和错误上下文。

执行示例：

```powershell
python scripts/capture_web_screenshots.py `
  --plan CASE_DIR/02-evidence/screenshot-plan.json `
  --output CASE_DIR/02-evidence/screenshots `
  --evidence-source CASE_DIR/02-evidence/evidence-graph.json `
  --evidence-output CASE_DIR/02-evidence/evidence-graph.with-screenshots.json `
  --fail-fast
```

不要在页面尚未稳定时用固定长等待替代状态判断。`wait_ms` 只处理明确的短暂视觉收尾，业务完成状态使用选择器或文本断言。

## 6. 质量检查

每张图片写入 `screenshot-index.json`，包括：

- 路由、角色、标题、采集时间、HTTP 状态；
- 文件路径、SHA-256、宽高、模式；
- 灰度均值、标准差、熵、近白比例、内容比例和 dHash；
- 近重复图片、尺寸不足、疑似空白和内容过少提示；
- 控制台、页面异常、请求失败和失败快照。

默认质量提示会让命令返回非零状态。仅在人工确认提示可接受后使用 `--allow-quality-warnings`。近重复图片只在两个步骤确实需要展示同一状态时设置 `allow_duplicate: true`。

## 7. Computer Use 收据

桌面端、Electron、模拟器或复杂交互由 Agent 工具完成实际操作，并把结果写入 `computer-use-session.json`。收据至少包含应用启动结果、是否需要登录及登录结果、每张图对应的动作结果、保存后的图片路径、角色、窗口/URL与时间。示例与架构位于：

- `assets/examples/computer-use-session.example.json`
- `assets/schemas/computer-use-session.schema.json`

确定性收尾命令：

```powershell
python scripts/finalize_agent_screenshots.py `
  --plan CASE_DIR/screenshot-plan.json `
  --session CASE_DIR/computer-use-session.json `
  --output CASE_DIR/screenshots `
  --report CASE_DIR/screenshot-index.json `
  --evidence-source CASE_DIR/evidence-graph.json `
  --evidence-output CASE_DIR/evidence-graph.with-screenshots.json
```

该脚本检查收据结构、启动/登录/动作、图片解码、尺寸、清晰度、SHA-256、时间、角色、URL/窗口和证据映射。项目保留轻量确定性测试，不维护依赖桌面焦点与DPI的 GUI E2E。

## 8. 状态与发布语义

- `screenshot_policy=required`：正式资料默认值；任何缺图、跳过、启动失败、登录失败或断言失败都会在截图阶段暂停后续材料生成；
- `screenshot_policy=draft_allowed` + `mode=skip, state=skipped_by_user`：用户明确只要草稿，允许带占位符草稿；
- `state=awaiting_capture`：尚未执行截图，正式发布失败；
- `state=failed`：截图、质量或收据校验失败，正式发布失败；
- `state=captured`：计划中的每张图均存在并通过文件、哈希、清晰度、上下文和映射检查。

非 `skip` 模式下，空 `captures` 列表始终失败；不能依赖空列表上的 `all(...)` 得到通过结论。截图计划保存 `input_sha256`，由业务清单、源码、地址、模式、服务启动参数和构建指纹组成；指纹变化时旧计划、索引和图片会先归档。用户自行截图按计划 ID 写回，使手册中的 `screenshot_ids` 可以直接找到对应图片。

## 9. 证据图谱回写

使用 `--evidence-source` 与 `--evidence-output` 成对参数，将已采集截图写为 `SHOT-<id>` 节点，并按 `evidence_ids` 建立 `supports` 边。保留原图谱不变，先审阅新图谱，再替换正式事实源。

手册正文只引用质量通过、内容已核验的截图。图题使用计划中的 `title`；内部证据标识留在 JSON 和校验报告中，不显示在正式 Word 页面。

## 10. 故障处理

- 健康检查失败：核对启动目录、端口、环境变量和应用日志。
- 登录后仍回到登录页：更新 storage state 或重新完成验证码/单点登录。
- 页面持续有请求：降低 `network_idle_timeout_ms`，并依赖 `ready_selector` 和断言。
- 截图为空白：检查前端运行时错误、路由权限、Canvas/WebGL 加载和浏览器通道。
- 图片重复：确认动作是否真正改变状态，检查测试数据、保存结果和选择器。
- 元素被裁切：改用 `selector` 截图或适度调整 viewport；不拉伸截图。
- 敏感数据未遮盖：补充 `mask`，重新采集并作废旧图及其哈希记录。
