<p align="center">
  <img width="180"  alt="exec-1908e30b-b18f-440b-bd7f-5c282be37a79" src="https://github.com/user-attachments/assets/61615b6e-9ab8-4a87-a57a-f42c53d0b897" />

</p>

<h1 align="center">Software Certificate Skill</h1>

<p align="center">这是一款面向真实项目的软件著作权申请资料生成 Skill，它能够自动分析项目、提取业务证据、选择源码，生成申请表、操作手册、代码材料，并输出可检查的 DOCX 与 PDF。中间证据和诊断文件写入系统临时目录，项目中只保留正式资料。</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=FFD43B" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/Agent-Multi--Platform-00A67E?style=flat-square&logo=probot&logoColor=white" alt="Multi-Agent">
  <img src="https://img.shields.io/badge/Output-DOCX%20%7C%20PDF%20%7C%20TXT-2B579A?style=flat-square&logo=microsoftword&logoColor=white" alt="DOCX, PDF and TXT output">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-F59E0B?style=flat-square&logo=opensourceinitiative&logoColor=white" alt="MIT License"></a>
</p>




## 快速使用

在已安装此 Skill 的 Agent 中打开目标项目，输入：

```text
为当前项目生成软件著作权申请资料
```

Agent 会一次收集登记事实并让你选择截图方式，随后完成项目分析、材料生成、PDF 转换、逐页检查和打包。最终交付位置和待确认事项会直接告知你。

## 交付物

```text
正式资料/
├─ 申请表信息.txt
├─ <软件全称>_操作手册.docx / .pdf
├─ <软件全称>-代码(前30页).docx / .pdf   # 源码达到60页时
├─ <软件全称>-代码(后30页).docx / .pdf   # 源码达到60页时
└─ <软件全称>-代码(全部).docx / .pdf     # 源码不足60页时
```

- `申请表信息.txt`：复制到登记系统。
- DOCX：用于 Word/WPS 检查和编辑；PDF：按登记系统要求上传。
- 代码材料由完整源文件组成，保留原文件、原始行号和哈希，可追溯。

## 示例

下面是一次从登记事实收集到正式材料生成的示例流程。图片仅用于说明界面和交付效果，示例中的登记信息仍需替换为真实事实。

<table>
  <tr>
    <td align="center" width="50%"><img width="1225" height="1383" alt="image" src="https://github.com/user-attachments/assets/6c4de6f0-d51b-4481-9a95-e7969372abbe" /><sub>发起资料生成任务</sub>
</td>
    <td align="center" width="50%"><img width="1210" height="1398" alt="image" src="https://github.com/user-attachments/assets/a185ebe8-4286-48ff-8ae8-974e089c4ea9" /><sub>确认软件名称、版本和开发事实</sub>

</td>
  </tr>
  <tr>
    <td align="center" ><img width="1197" height="1395" alt="image" src="https://github.com/user-attachments/assets/01401dfd-77e1-4429-9ac8-a439ea7933c4" />
<sub>生成结果与正式资料目录</sub>
    <td align="center"><img width="864" height="1213" alt="image" src="https://github.com/user-attachments/assets/e8dd0fcb-affc-48dd-88ce-75fea29f6ea5" /><sub>操作手册页面</sub>
</td>
  </tr>
  <tr>
    <td align="center"><img width="861" height="1226" alt="image" src="https://github.com/user-attachments/assets/2d517635-4fd8-482f-b0fd-7996d6d0972c" />
<sub>操作手册页面</sub>
</td>
    <td align="center"><img width="857" height="1217" alt="image" src="https://github.com/user-attachments/assets/5ac121cf-c589-4f25-b9ec-7cb99420723f" />
<sub>代码材料前30页</sub></td>
  </tr>
  <tr>
    <td align="center"><img width="789" height="1119" alt="image" src="https://github.com/user-attachments/assets/177c2831-1fa5-4e07-a63c-c587d53c1c30" />
<sub>代码材料后30页</sub></td>
    <td align="center"><img width="790" height="1117" alt="image" src="https://github.com/user-attachments/assets/9a1d8346-8a65-41ac-bbfc-7053e906cda4" />
<sub>代码材料后30页</sub></td>
  </tr>
    <tr>
    <td align="center"><img width="2031" height="1200" alt="image" src="https://github.com/user-attachments/assets/7d064e75-0e29-4327-8f22-54bf292c00ba" />
<sub>申请表信息</sub></td>
    <td align="center"><img width="2031" height="1200" alt="image" src="https://github.com/user-attachments/assets/0b55d3af-681a-418c-9f7d-48545dd49341" />
<sub>申请表信息</sub></td>
  </tr>
</table>

## 安装

```powershell
git clone https://github.com/IvanCodesDev/software-certificate-skill.git
cd software-certificate-skill
python scripts/install_agent_skill.py --platform all --scope project --project E:\path\to\project --force
```

支持 Codex、Claude Code、Cursor、OpenCode、WorkBuddy、QoderWork、TraeWork等多款Agent平台，只预览不写入：

```powershell
python scripts/install_agent_skill.py --platform all --scope project --project E:\path\to\project --dry-run
```

## 独立运行

安装基础依赖：

```powershell
python -m pip install -r requirements.txt
```

需要 Web 自动截图时再安装：

```powershell
python -m pip install "playwright>=1.49,<2"
python -m playwright install chromium
```

准备、继续、查看状态或回滚：

```powershell
python scripts/product_workflow.py prepare --project E:\path\to\project
python scripts/product_workflow.py resume --project E:\path\to\project
python scripts/product_workflow.py status --project E:\path\to\project
python scripts/product_workflow.py rollback --project E:\path\to\project
```

`prepare` 生成集中表单、截图选择卡和内部分析任务；完成表单后运行 `resume`。PDF 转换需要 LibreOffice 或 Microsoft Word（优先 LibreOffice）；逐页渲染需要 Poppler 的 `pdftoppm` 或 `pdftocairo`。

## 处理范围与约束

- 扫描 README、配置、路由、页面、组件、服务、API、模型、测试、部署和既有截图，功能结论须能回溯到证据或真实运行结果。
- 支持 Chrome DevTools、Computer Use、用户自行截图和跳过截图四种路线；截图会记录计划、哈希和章节映射。
- 手册按真实功能组织章节，使用 A4、黑白灰、常规办公排版；内容充分的项目通常为 40–60 页，小项目按证据量缩短。
- 代码达到 60 页时输出前/后 30 页两卷，否则输出“代码(全部)”一卷。
- 表单、手册、代码页眉、文件名和报告来自同一份已确认事实；支持断点续作、局部重建和最近版本恢复。
- 规则快照见 [`references/rules-2026.md`](references/rules-2026.md) 和 [`assets/rules/rules-snapshot.json`](assets/rules/rules-snapshot.json)。规则记录区分法定要求与经验建议，并保存来源和日期。

## 测试

测试会在系统临时目录创建脱敏最小项目，结束后自动清理，不依赖仓库内置 Demo：

```powershell
python -m unittest discover -s tests -v
python scripts/validate_skill.py
python scripts/self_test.py --workdir "${TEMP}/software-certificate-self-test"
```

## 贡献与免责声明

本项目使用 [MIT License](LICENSE)。提交 Issue/PR 前请阅读 [贡献指南](.github/CONTRIBUTING.md)。不得提交真实证件、账号、密钥、未授权源码或可识别申请材料；测试夹具必须脱敏；新增规则需标注来源等级和日期。

本项目只负责材料整理、排版和质量检查，不替代申请人核验权属、原创性、事实真实性及登记系统当日要求。提交前请逐项复核登记事实和声明。
