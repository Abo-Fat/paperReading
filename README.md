# paperReading

面向 IC 设计/制造方向的**论文精读 PPT 自动生成工具**。

读入论文 PDF，抽取文本与页面截图，再根据结构化 outline JSON 自动生成**白底、上文下图**固定版式的中文文献阅读报告 `.pptx`。

---

## 目录结构

```
paperReading/
├── scripts/
│   ├── extract_pdf_context.py   # 从 PDF 抽取文本与渲染图
│   └── build_paper_reading_ppt.py  # 依据 outline.json 生成 PPTX
├── references/
│   └── outline_example.json    # outline.json 格式示例
├── evals/
│   └── evals.json              # 技能评估用例
└── SKILL.md                    # Claude Code 技能描述文件
```

---

## 环境依赖

```bash
pip install pypdf pypdfium2 pillow python-pptx
```

| 包 | 用途 |
|---|---|
| `pypdf` | 提取 PDF 文本 |
| `pypdfium2` | 将 PDF 页面渲染为 PNG |
| `pillow` | 图片裁剪与尺寸适配 |
| `python-pptx` | 创建/修改 PPTX |

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
| `rendered_pages/page_XXXX.png` | 每页渲染图 |
| `rendered_pages/first_page_title_authors.png` | 首页标题/作者区域截图（上 38%）|
| `extraction_manifest.json` | 提取清单，记录所有产物路径 |

可选参数：

```
--start-page N   # 从第 N 页开始处理（默认 1）
--end-page   N   # 到第 N 页结束（默认 0 = 最后一页）
```

---

### 第 2 步：编写 outline.json

参考 [references/outline_example.json](references/outline_example.json)，按以下结构组织每张幻灯片：

```jsonc
{
  "paper": {
    "title": "论文标题",
    "authors": "作者列表",
    "venue_year": "会议/期刊 年份"
  },
  "slides": [
    {
      "title": "整篇论文的核心 Motivation",
      "text_lines": ["背景：...", "动机：...", "创新概览：..."],
      "images": ["paper_assets/rendered_pages/first_page_title_authors.png"],
      "figure_refs": ["Title + Authors"]
    },
    {
      "title": "Motivation 1 -> Innovation 1",
      "motivation": "痛点描述",
      "innovation": "对应创新",
      "text_lines": ["证据链：...", "图示说明：Fig. X ..."],
      "images": ["paper_assets/rendered_pages/page_0003.png"],
      "figure_refs": ["Fig. 3"]
    }
    // ... 更多 Motivation/Innovation 对 ...
    // 倒数第二页：可能遗漏的小创新点
    // 最后一页：总结
  ]
}
```

**幻灯片顺序规范：**

1. 首页：整篇论文总 Motivation（配标题/作者截图）
2. 每个 Motivation → Innovation 对独立一页
3. 可能遗漏的小创新点
4. 总结页

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
- **上方约 1/3**：标题 + 文字区（Calibri，标题 30pt 加粗，正文 16pt）
- **下方约 2/3**：配图区（最多 3 张并排，自动居中适配比例）

每张图下方自动添加图号字幕（10pt 灰色居中）。

---

## 与 Claude Code 技能集成

本项目包含 [SKILL.md](SKILL.md)，可作为 Claude Code 自定义技能加载。  
触发关键词：论文精读、文献阅读报告、Motivation/Innovation 分析、按 Fig 讲解、生成 .pptx 等。

技能会引导 Claude 按照"提取 → outline → 生成 PPT"三步流程执行，并自动遵守内容约束与质量检查清单。
