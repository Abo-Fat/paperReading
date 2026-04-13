# paperReading

面向 IC 设计/制造方向的**论文精读 PPT 自动生成工具**。

读入论文 PDF，抽取文本与单独 Fig 图片，再根据结构化 outline JSON 自动生成**白底、上文下图**固定版式的中文文献阅读报告 `.pptx`。

---

## 目录结构

```
paperReading/
├── scripts/
│   ├── extract_pdf_context.py      # 从 PDF 抽取文本、渲染图、单独 Fig
│   └── build_paper_reading_ppt.py  # 依据 outline.json 生成 PPTX
├── references/
│   └── outline_example.json        # outline.json 格式示例
├── evals/
│   └── evals.json                  # 技能评估用例
└── SKILL.md                        # Claude Code 技能描述文件
```

---

## 环境依赖

```bash
pip install pypdf pypdfium2 pymupdf pillow python-pptx
```

| 包 | 用途 |
|---|---|
| `pypdf` | 提取 PDF 文本 |
| `pypdfium2` | 将 PDF 页面渲染为整页 PNG |
| `pymupdf` | 从 PDF 提取单独 Fig 图片（推荐安装）|
| `pillow` | 图片裁剪与尺寸适配 |
| `python-pptx` | 创建/修改 PPTX |

> `pymupdf` 为可选但强烈推荐：安装后 PPT 配图将使用单独裁剪的 Fig，而非整页截图。

---

## 使用流程

### 第 1 步：提取 PDF 内容

```bash
python paperReading/scripts/extract_pdf_context.py \
  --pdf "path/to/paper.pdf" \
  --out-dir "paper_assets" \
  --dpi 220
```

**产物（写入 `paper_assets/`）：**

| 文件 | 说明 |
|---|---|
| `text_by_page.json` | 逐页文本及图号引用列表 |
| `rendered_pages/page_XXXX.png` | 每页整页渲染图（备用）|
| `rendered_pages/first_page_title_authors.png` | 首页标题/作者区域截图（上 38%）|
| `rendered_pages/figures/fig_pXXXX_YY.png` | **单独 Fig 图片**（由 pymupdf 提取）|
| `extraction_manifest.json` | 提取清单，含 `figures_by_page` 字段（页码 → Fig 路径列表）|

`figures_by_page` 示例：

```json
{
  "3": ["…/figures/fig_p0003_00.png", "…/figures/fig_p0003_01.png"],
  "5": ["…/figures/fig_p0005_00.png"]
}
```

可选参数：

```
--start-page N   # 从第 N 页开始处理（默认 1）
--end-page   N   # 到第 N 页结束（默认 0 = 最后一页）
```

---

### 第 2 步：编写 outline.json

参考 [references/outline_example.json](references/outline_example.json)。中间分析页使用**四层次字段**，首页和最后一页优先使用 `text_lines` 写叙事型内容：

```jsonc
{
  "paper": {
    "title": "论文标题",
    "authors": "作者列表",
    "venue_year": "会议/期刊 年份"
  },
  "slides": [
    {
      "title": "研究背景与动机",
      "text_lines": [
        "该方向面向……应用场景，核心关注指标是……。",
        "现有方法主要受限于……，导致……问题持续存在。",
        "这些限制使得……场景下的性能/成本/可扩展性难以兼顾。",
        "因此，本文的核心研究动机是……。"
      ],
      "images": ["paper_assets/rendered_pages/first_page_title_authors.png"],
      "figure_refs": ["Title + Authors"]
    },
    {
      "title": "Motivation 1 → Innovation 1：时序收敛",
      "motivation": "传统方法布局后时序退化，迭代次数多。",
      "innovation": "跨层协同建模 + 自适应约束更新机制。",
      "methods":    "时序感知损失函数，联合优化关键路径与扇出。",
      "results":    "后仿误差降低 42%，迭代轮数从 4.2 减至 1.8（Fig. 3）。",
      "images":     ["paper_assets/rendered_pages/figures/fig_p0003_00.png"],
      "figure_refs": ["Fig. 3"]
    }
    // ... 更多 Motivation/Innovation 对 ...
    // 倒数第二页：可能遗漏的小创新点（可用 text_lines）
    // 最后一页：总结 + 评价（优先用 text_lines）
  ]
}
```

**四层次字段说明（仅中间分析页强制使用）：**

| 字段 | PPT 标签颜色 | 内容要求 |
|------|-------------|----------|
| `motivation` | 深红 | 该页核心痛点/背景缺陷（1-2 句）|
| `innovation` | 深蓝 | 对应创新方案核心思路（1-2 句）|
| `methods` | 深绿 | 关键技术实现路径，含参数/流程要点（1-2 句）|
| `results` | 紫色 | 量化结论/对比数据，引用原文数值（1-2 句）|

> `text_lines` 字段仍受支持（向后兼容），当四个字段均缺失时作为 fallback 渲染。推荐将首页和最后一页都写成 `text_lines`。

**幻灯片顺序规范：**

1. 首页：详细讲清故事背景与研究动机（配标题/作者截图，不套四层次）
2. 每个 Motivation → Innovation 对独立一页（配单独 Fig 图片）
3. 可能遗漏的小创新点
4. 总结 + 评价页（不套四层次）

---

### 第 3 步：生成 PPTX

```bash
python paperReading/scripts/build_paper_reading_ppt.py \
  --outline-json "outline.json" \
  --output "result.pptx"
```

**在已有 PPTX 上追加（修改场景）：**

```bash
python paperReading/scripts/build_paper_reading_ppt.py \
  --outline-json "outline.json" \
  --base-pptx "existing.pptx" \
  --output "updated.pptx"
```

**全部参数：**

| 参数 | 说明 |
|---|---|
| `--outline-json` | outline JSON 路径（必填）|
| `--output` | 输出 PPTX 路径（必填）|
| `--base-pptx` | 基础 PPTX（可选，用于追加模式）|
| `--clear-existing` | 配合 `--base-pptx` 使用，先清空原有幻灯片 |
| `--force-16x9` | 强制输出 16:9 尺寸 |

---

## PPT 版式说明

所有页面统一采用：

- 纯白背景
- **上方约 1/3**：标题（Calibri 20pt 加粗）+ 文字区
- 中间分析页文字区：四层次结构（标签 13pt 彩色加粗，内容 13pt 黑色）
- 首页与最后一页文字区：优先使用 `text_lines` 组织成逻辑清楚的叙事/总结评价短句
- **下方约 2/3**：配图区（最多 3 张并排，自动居中适配比例）

每张图下方自动添加图号字幕（10pt 灰色居中）。

---

## AI 编程助手集成

本项目包含 [SKILL.md](SKILL.md)，可将论文精读工作流注册为 AI 助手的自定义技能。  
触发关键词：论文精读、文献阅读报告、Motivation/Innovation 分析、按 Fig 讲解、生成 .pptx 等。

### 安装方法

**第 1 步：克隆仓库**

```bash
git clone https://github.com/<your-username>/<repo-name>.git
```

**第 2 步：将 SKILL.md 放到助手的技能目录**

根据你使用的 AI 编程助手选择对应路径：

| 助手 | 技能目录 |
|---|---|
| Claude Code | `~/.claude/skills/paper-reading/SKILL.md` |
| OpenAI Codex CLI | `~/.codex/skills/paper-reading/SKILL.md` |

```bash
# Claude Code
mkdir -p ~/.claude/skills/paper-reading
cp <repo-name>/SKILL.md ~/.claude/skills/paper-reading/SKILL.md

# OpenAI Codex CLI
mkdir -p ~/.codex/skills/paper-reading
cp <repo-name>/SKILL.md ~/.codex/skills/paper-reading/SKILL.md
```

**第 3 步：重启助手**，技能即生效。

---

安装完成后，技能会引导 AI 按照"提取 → outline → 生成 PPT"三步流程执行，并自动遵守内容约束与质量检查清单。
