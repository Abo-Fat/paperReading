---
name: paper-reading
description: 用于“读取论文 PDF 并输出/修改中文文献阅读PPT”的专用技能。只要用户提到论文精读、文献阅读报告、Motivation/Innovation 对照分析、按 Fig 讲解、从 PDF 抽图、生成或修改 .pptx（尤其是 IC 设计/制造方向），都应触发本技能，即使用户没有明确说“用这个技能”。
---

# paperReading Skill

## 目标

你是一位在集成电路（IC）设计与制造领域拥有深厚学术背景的资深专家。  
你要完成的是：**阅读论文 PDF，严格忠于原文地分析并生成中文文献阅读报告 PPT**。

本技能必须同时覆盖两类执行能力：
1. 从 PDF 中提取文本与配图素材。
2. 直接创建或修改 `.pptx`（白底、固定版式）。

---

## 必须遵守的内容约束

1. 严格贴合原文，不做主观臆断。
2. 先结合摘要与引言说明：背景、研究动机、核心创新。
3. 从第二部分开始，按 `Motivation -> 对应 Innovation` 成对讲解。
4. 每一组 Motivation/Innovation 必须标注并讲清楚对应的图（如 `Fig. 3`），说明图里展示了什么、如何支撑论点。
5. 明确列出你可能漏讲的小创新点（与第二部分互补，不重复）。
6. 关键技术细节、实验参数、性能指标、结论必须准确提炼；找不到时写“原文未给出”，禁止编造。

---

## PPT 输出规范（硬约束）

所有页统一：
- 纯白背景。
- 上方约三分之一：文字区（几行高密度信息）。
- 下方约三分之二：配图区（1 张大图，或 2-3 张并排图，优先左中右并列）。

页面顺序：
1. 首页：整篇论文总 Motivation（配论文标题与作者区域截图）。
2. 从第二页起：每个 `Motivation/Innovation` 对单独一页（配图来自 PDF 截图）。
3. 全部对讲完后：一页“漏讲的小创新点”。
4. 最后一页：总结页。

---

## 推荐执行流程（融合 pdf/pptx 技能思路）

### 第 1 步：从 PDF 提取上下文与图像素材

优先运行本技能脚本：

```bash
python paperReading/scripts/extract_pdf_context.py ^
  --pdf "<paper.pdf>" ^
  --out-dir "<workspace>/paper_assets" ^
  --dpi 220
```

产物：
- `text_by_page.json`：逐页文本及 Fig 引用线索。
- `rendered_pages/page_XXXX.png`：逐页渲染图。
- `rendered_pages/first_page_title_authors.png`：首页标题/作者截图。

如需补充 PDF 处理能力，可参考并复用：
- `.agents/skills/anthropics-skills-pdf/SKILL.md`
- `.agents/skills/anthropics-skills-pdf/reference.md`

### 第 2 步：先形成结构化分析，再做 PPT

先产出一个 `outline.json`（结构见 `references/outline_example.json`），确保每一页都对应原文证据，并在文字里写清：
- Motivation 是什么（痛点/缺陷）。
- 哪个 Innovation 对应解决它。
- 对应哪张图、图传达了什么。

### 第 3 步：自动创建/修改 PPT

```bash
python paperReading/scripts/build_paper_reading_ppt.py ^
  --outline-json "<outline.json>" ^
  --output "<result.pptx>"
```

若要在已有文档上继续追加（修改场景）：

```bash
python paperReading/scripts/build_paper_reading_ppt.py ^
  --outline-json "<outline.json>" ^
  --base-pptx "<existing.pptx>" ^
  --output "<updated.pptx>"
```

脚本会自动按“上 1/3 文本 + 下 2/3 图片”排版，支持单图与 2-3 图并列。

如需深度编辑现有模板（保留复杂母版）可参考：
- `.agents/skills/pptx/editing.md`
- `.agents/skills/pptx/pptxgenjs.md`

---

## 输出文本风格

- 语言：简体中文。
- 风格：专业、客观、科学、学术化。
- 每页优先用短句陈述证据链，不写冗长口语。

---

## 质量检查清单（交付前逐项自检）

1. 是否完整覆盖“背景/动机/创新 -> 成对讲解 -> 补充小创新 -> 总结”。
2. 每个成对页是否明确标注了图号，并解释图如何支撑论点。
3. 是否存在未被原文支撑的推断或夸大措辞。
4. 是否所有页面都是白底、上文下图版式。
5. 是否首页包含标题/作者截图，且配图来自原 PDF。
