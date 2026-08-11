# 多 Agent 平台适配

快照日期：2026-08-11。

## 设计目标

核心能力只维护在一个标准 `SKILL.md`、`scripts/`、`references/` 和 `assets/` 中。平台适配器只负责发现入口和工作区规则，不复制或改写业务知识。运行脚本时始终把路径解析到实际 Skill 根目录。

## 支持矩阵

| 平台 | 项目级入口 | 用户级入口 | 适配方式 |
|---|---|---|---|
| Codex | `.agents/skills/software-certificate-skill/` + `AGENTS.md` | `~/.codex/skills/software-certificate-skill/` | 原生个人 Skill；项目使用通用 Skill |
| Claude Code | `.claude/skills/software-certificate-skill/` | `~/.claude/skills/software-certificate-skill/` | 原生 Agent Skill |
| Cursor | `.cursor/skills/software-certificate-skill/` | `~/.cursor/skills/software-certificate-skill/` | 原生 Agent Skill；旧版本可结合项目 Rules |
| OpenCode | `.opencode/skills/software-certificate-skill/` | `~/.config/opencode/skills/software-certificate-skill/` | 原生 Agent Skill |
| WorkBuddy | `.agents/skills/software-certificate-skill/` + `AGENTS.md` | `~/.agents/skills/software-certificate-skill/` | 通用 Skill 与工作区指令 |
| QoderWork | `.agents/skills/software-certificate-skill/` + `.qoder/rules/` | `~/.agents/skills/software-certificate-skill/` | 通用 Skill、AGENTS 与项目规则 |
| TraeWork | `.agents/skills/software-certificate-skill/` + `.trae/rules/` | `~/.agents/skills/software-certificate-skill/` | 通用 Skill、AGENTS 与项目规则 |

WorkBuddy、QoderWork 和 TraeWork 的产品形态与工作区规则可能随版本变化；通用入口不依赖某个私有工具调用。若平台版本已原生支持 Agent Skills，可直接把 `.agents/skills/software-certificate-skill` 加入其 Skill 搜索路径。

## 一次安装全部平台

```powershell
python scripts/install_agent_skill.py `
  --platform all `
  --scope project `
  --project TARGET_PROJECT `
  --force `
  --report TARGET_PROJECT/.agents/software-certificate-skill-install.json
```

只安装单个平台：

```powershell
python scripts/install_agent_skill.py --platform claude-code --scope project --project TARGET_PROJECT
python scripts/install_agent_skill.py --platform codex --scope project --project TARGET_PROJECT
python scripts/install_agent_skill.py --platform cursor --scope project --project TARGET_PROJECT
python scripts/install_agent_skill.py --platform opencode --scope project --project TARGET_PROJECT
python scripts/install_agent_skill.py --platform workbuddy --scope project --project TARGET_PROJECT
python scripts/install_agent_skill.py --platform qoderwork --scope project --project TARGET_PROJECT
python scripts/install_agent_skill.py --platform traework --scope project --project TARGET_PROJECT
```

先预览写入位置：

```powershell
python scripts/install_agent_skill.py --platform all --scope project --project TARGET_PROJECT --dry-run
```

安装器不会覆盖已存在的 Skill；显式使用 `--force` 时，先在同目录构建完整临时副本，再原子替换旧版本。`AGENTS.md` 使用带标记的托管区块，只更新本 Skill 的区块，保留项目原有内容。

## 平台无关调用约定

任意平台都应遵循以下过程：

1. 定位 `software-certificate-skill/SKILL.md`；
2. 把其父目录记为 `SKILL_ROOT`；
3. 从 `SKILL_ROOT` 解析所有 `scripts/`、`references/` 和 `assets/` 路径；
4. 从真实项目建立案例目录，不在 Skill 安装目录内生成申请案例；
5. 优先运行已有确定性脚本，平台工具只负责文件、浏览器、终端和 Word/PDF 交互；
6. 无浏览器工具时使用 Playwright 截图计划；平台拥有浏览器工具时仍输出相同 `screenshot-index.json` 字段。

## 官方入口

- Claude Code Skills：<https://code.claude.com/docs/en/skills>
- Codex Skills：<https://developers.openai.com/codex/skills>
- Cursor Skills：<https://cursor.com/docs/context/skills>
- OpenCode Agent Skills：<https://opencode.ai/docs/skills/>
- WorkBuddy：<https://www.workbuddy.ai/>
- Qoder 文档：<https://docs.qoder.com/>
- Trae / TraeWork 文档：<https://docs.trae.ai/>

每次发布适配器前重新核对官方入口。已公开的原生 Skill 目录优先；没有稳定公开目录时使用通用 `.agents/skills` 与项目规则，而不声称存在专有原生接口。
