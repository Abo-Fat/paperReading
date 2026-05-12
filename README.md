<!-- Author: Albert@PKU -->
<!-- Date: 2026/05 -->
# paperReading

面向 IC 设计/制造方向的论文精读 PPT 自动生成工具。
流程是：读取论文 PDF，抽取独立 Fig/Table 图片，再根据 outline.json 自动生成白底、上下分区的中文文献阅读报告 `.pptx`。

---

## 目录结构

```text
paperReading/
├── scripts/
│   ├── extract_paper_fig.py      # 从 PDF 抽取独立 Fig/Table 图片
│   ├── extract_pdf_context.py    # 辅助提取文本、整页渲染、首页截图
│   └── build_paper_reading_ppt.py# 根据 outline.json 生成 PPTX
├── references/
│   └── outline_example.json      # outline.json 示例
├── evals/
│   └── evals.json                # 技能评估用例
└── SKILL.md                      # 技能说明
```

---

## 环境依赖

```bash
pip install pypdf pypdfium2 pymupdf pillow python-pptx
```

| 包 | 用途 |
|---|---|
| `pypdf` | 提取 PDF 文本 |
| `pypdfium2` | 将 PDF 页面渲染成整页 PNG |
| `pymupdf` | 从 PDF 中提取独立 Fig/Table 图片 |
| `pillow` | 图片裁切与尺寸适配 |
| `python-pptx` | 创建 / 修改 PPTX |

---

## 使用流程

### 第 1 步：抽取独立图片

优先使用新的抽图脚本：

```bash
python paperReading/scripts/extract_paper_fig.py \
  "path/to/paper.pdf" \
  "paper_assets/<pdf_stem>_figures" \
  --dpi 180
```

输出文件：

| 文件 | 说明 |
|---|---|
| `pXX_FigN.png` | 论文中的独立 Fig 图片 |
| `pXX_TableN.png` | 论文中的 Table 图片 |

如果还需要整页渲染、首页标题/作者截图或逐页文本，可再辅助运行：

```bash
python paperReading/scripts/extract_pdf_context.py \
  --pdf "path/to/paper.pdf" \
  --out-dir "paper_assets" \
  --dpi 220
```

### 第 2 步：编写 outline.json

参考 [references/outline_example.json](references/outline_example.json)。

建议规则：

- 首页优先写成 `rendered_pages/first_page_title_authors.png`。
- 中间分析页优先引用 `pXX_FigN.png`。
- 如果某页更适合表格，也可以直接引用 `pXX_TableN.png`。
- 最后一页同时包含总结和评价，评价必须基于原文证据。

### 第 3 步：生成 PPTX

```bash
python paperReading/scripts/build_paper_reading_ppt.py \
  --outline-json "path/to/outline.json" \
  --output "output_dir"
```

如果要基于已有 PPTX 继续修改：

```bash
python paperReading/scripts/build_paper_reading_ppt.py \
  --outline-json "path/to/outline.json" \
  --base-pptx "path/to/existing.pptx" \
  --output "output_dir"
```

---

## 说明

- 输出 PPT 默认采用白底、上文本下配图布局。
- 配图优先使用独立裁切的 Fig/Table，不要直接放整页截图。
- 如果某页没有可用图片，才退回到整页截图或纯文本页。
