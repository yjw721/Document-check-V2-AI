# -*- coding: utf-8 -*-
"""回归：知识库 RAG 切片质量（v4 表格感知）。

背景：这批月度文档（市场风险溢价 / LPR / 定期存款）的核心内容是**数值表格**，
旧切片实现暴露三类缺陷，本测试逐条锁死，防止未来改 ``text.py`` 时回退：

    1. ``parse_docx`` 先遍历 ``doc.paragraphs`` 再遍历 ``doc.tables`` ——
       所有表格被挪到文末、脱离所属标题，LPR 表被挂在「三、定期存款」下；
    2. ``_tail_snippet`` 按固定字数截尾 —— 表格行无句读，会切在数字中间
       （``...3.06`` 截出 ``06 |``），同一残片在后续每块重复；
    3. ``MIN_CHUNK_CHARS`` 定义但从未使用 —— 13 字碎块、孤立标题块。

覆盖：
    A. 构造 docx（现场用 python-docx 生成）→ 文档流交错顺序 / 合并单元格去重；
    B. 表格被块大小切开时，续块自动补表头行（片段不再是一排裸数字）；
    C. 重叠只回填完整行、不跨表格行（无 ``06 |`` 类数字中部残片）；
    D. 过短片段合并：无 < MIN_CHUNK_CHARS 碎块、无孤立标题块；
    E. 所有块的首行都是原始行（截断不产生半行）；
    F. 真实知识库文件冒烟（d_1088aa227b.docx，若存在）。

隔离策略：不触碰真实 config/knowledge/ —— A~E 全程用内存构造的文档；
F 只读 ``config/knowledge/files/`` 下的原始文件（不写、不入库）。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

OK = FAIL = 0


def chk(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


from checkers.rag.parsers.office import parse_docx          # noqa: E402
from checkers.rag.text import (                             # noqa: E402
    CHUNK_SCHEMA_VERSION,
    MIN_CHUNK_CHARS,
    chunk_blocks,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_DOCX = os.path.join(ROOT, "config", "knowledge", "files", "d_1088aa227b.docx")


def build_docx() -> bytes:
    """现场构造一份结构等价的月度文档。

    特意包含四类结构陷阱：
      - 「如下表所示：」引导句 + 紧随其后的数值表（表1，含横向合并的横幅行）；
      - 宽表（表2，20 列 × 12 行，约 140 字/行）——必须被块大小切开；
      - 表与下一节标题之间仅隔一个短句；
      - 连续两个短标题（触发孤立标题块）+ 一个 10 字碎段。
    """
    from docx import Document

    doc = Document()
    doc.add_paragraph("2026年1月市场风险溢价、无风险报酬率")
    doc.add_paragraph("贷款市场报价利率、定期存款利率")
    doc.add_paragraph("评估基准日2026年1月31日，质控部公布如下：")
    doc.add_paragraph("一、市场风险溢价MRP（Rm-Rf）、无风险报酬率Rf")
    doc.add_paragraph("市场风险溢价是市场投资报酬率与无风险报酬率之差，以上证综指为基础测算；"
                      "无风险报酬率通过Wind金融终端查询国债到期收益率获得。")
    doc.add_paragraph("经测算，市场风险溢价和收益法中永续年期无风险报酬率如下表所示：")

    # 表1：第 1 行是横向合并的横幅 + 列头，第 2 行起为日期数据行
    t1 = doc.add_table(rows=9, cols=3)
    t1.cell(0, 0).merge(t1.cell(0, 2))          # 横向合并：解析后应去重为 1 列
    t1.cell(0, 0).text = "评估基准日"
    for i, (d, mrp, rf) in enumerate([
        ("2025/06/30", "6.38%", "1.93%"), ("2025/07/31", "6.45%", "1.97%"),
        ("2025/08/31", "6.58%", "2.12%"), ("2025/09/30", "6.72%", "2.01%"),
        ("2025/10/31", "6.55%", "2.18%"), ("2025/11/30", "6.42%", "2.22%"),
        ("2025/12/31", "6.39%", "2.32%"), ("2026/01/31", "6.49%", "2.34%"),
    ], 1):
        t1.cell(i, 0).text, t1.cell(i, 1).text, t1.cell(i, 2).text = d, mrp, rf
    # 注：合并后的第 0 行在 row.cells 中展开为 3 个重复值，表头列名用横幅覆盖

    doc.add_paragraph("其他未公布的有限年期无风险报酬率，请按匹配年期的国债到期收益率自行查询。")
    doc.add_paragraph("二、贷款市场报价利率LPR（%）")

    # 表2：宽表 —— 20 列 × 12 行，每行约 140 字，必被 size=500 切成多块
    months = ["3M", "6M", "1Y", "15M", "18M", "21M", "2Y", "27M", "30M", "33M",
              "3Y", "39M", "42M", "45M", "4Y", "51M", "54M", "57M", "5Y", "5Y+"]
    t2 = doc.add_table(rows=12, cols=20)
    for j, m in enumerate(months):
        t2.cell(0, j).text = m
        t2.cell(1, j).text = str(3 + j * 3)
    for i in range(2, 12):
        t2.cell(i, 0).text = f"2025/{i-1:02d}/20"
        for j in range(1, 20):
            t2.cell(i, j).text = f"{2.91 + j * 0.03:.2f}"

    doc.add_paragraph("三、主要银行人民币定期存款挂牌利率")
    t3 = doc.add_table(rows=5, cols=8)
    for j, h in enumerate(["银行名称", "发布日期", "三个月", "六个月", "一年", "二年", "三年", "五年"]):
        t3.cell(0, j).text = h
    for i, bank in enumerate(["中国银行", "中国工商银行", "中国建设银行", "中国农业银行"], 1):
        t3.cell(i, 0).text = bank
        t3.cell(i, 1).text = "2025/5/20"
        for j in range(2, 8):
            t3.cell(i, j).text = f"{0.65 + (j - 2) * 0.15:.2f}"

    doc.add_paragraph("四、附则")            # 连续短标题之一（下一段仅 10 字 → 碎块）
    doc.add_paragraph("本文件自发布之日起施行。")   # 12 字 < MIN_CHUNK_CHARS
    doc.add_paragraph("解释权")              # 孤立标题（3 字），内容在下一标题下
    doc.add_paragraph("五、其他")
    doc.add_paragraph("北京卓信大华资产评估有限公司质控部")
    doc.add_paragraph("2026年2月2日")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _first_lines(blocks):
    return [b[1].split("\n", 1)[0] for b in blocks]


def main():
    global OK, FAIL
    raw = build_docx()
    blocks = parse_docx("2026年1月.docx", raw)
    chunks = chunk_blocks(blocks, 500, 100)
    texts = [c["text"] for c in chunks]
    flat = [t for _, t in blocks]

    # ---------- A. 文档流交错顺序 ----------
    print("\n[A] 文档流交错顺序（段落与表格按 w:body 真实顺序交错）")
    def idx(pred):
        return next(i for i, (loc, t) in enumerate(blocks) if pred(loc, t))

    i_prose = idx(lambda loc, t: t.startswith("经测算"))
    i_t1 = idx(lambda loc, t: loc.startswith("表 1 第 1 行"))
    i_next = idx(lambda loc, t: t.startswith("其他未公布的"))
    chk("表1 紧随引导句、在下一节之前", i_prose < i_t1 < i_next,
        f"prose={i_prose} t1={i_t1} next={i_next}")

    i_h2 = idx(lambda loc, t: t.startswith("二、贷款"))
    i_t2 = idx(lambda loc, t: loc.startswith("表 2 第 1 行"))
    i_h3 = idx(lambda loc, t: t.startswith("三、主要银行"))
    i_t3 = idx(lambda loc, t: loc.startswith("表 3 第 1 行"))
    chk("表2 挂在「二、LPR」下、表3 挂在「三、定期存款」下", i_h2 < i_t2 < i_h3 < i_t3,
        f"h2={i_h2} t2={i_t2} h3={i_h3} t3={i_t3}")

    # 合并单元格去重：横幅行解析后不应出现连续重复值
    banner = next(t for loc, t in blocks if loc.startswith("表 1 第 1 行"))
    chk("合并单元格相邻去重", banner == "评估基准日", f"banner={banner!r}")

    # ---------- B. 同表续块自动补表头 ----------
    print("\n[B] 宽表被切开时续块补表头")
    # 判据：每个含 LPR 数据行的块都必须含列头（3M/6M/…）
    lpr_data_chunks = [c for c in chunks
                       if any(l.startswith("2025/") and "| 2.9" in l
                              for l in c["text"].split("\n"))]
    chk("宽表被切成多块", len(lpr_data_chunks) >= 2, f"n={len(lpr_data_chunks)}")
    missing_hdr = [c["text"][:60] for c in lpr_data_chunks if "3M" not in c["text"]]
    chk("每个 LPR 续块都带表头列名（3M/6M/…）", not missing_hdr, f"缺表头块={missing_hdr}")

    # ---------- C. 重叠不切数字、不跨表格行 ----------
    print("\n[C] 重叠安全性（无数字中部残片、不跨表格行回填）")
    bad_frag = [t[:40] for t in texts
                if any(l.startswith(("6 |", "06 |", "3.0", "% |")) for l in t.split("\n"))]
    chk("无 '06 |' 类数字中部残片", not bad_frag, f"残片={bad_frag}")
    cross = []
    for t in texts:
        lines = [l for l in t.split("\n") if l.strip()]
        if len(lines) >= 2:
            a, b = lines[-2], lines[-1]
            if (" | " in a) != (" | " in b) and (" | " not in a) and len(a) < 40:
                cross.append((a[:30], b[:30]))
    chk("无「散文行+表格行」粘连的重叠尾巴", not cross, f"粘连={cross}")

    # ---------- D. 过短片段 / 孤立标题 ----------
    print("\n[D] 过短片段合并")
    tiny = [(i, t) for i, t in enumerate(texts) if len(t) < MIN_CHUNK_CHARS]
    chk(f"无 <{MIN_CHUNK_CHARS} 字碎块", not tiny, f"碎块={tiny}")
    orphan = [i for i, t in enumerate(texts)
              if len(t.split("\n")) == 1 and t.strip() in ("四、附则", "解释权", "五、其他")]
    chk("无孤立标题块", not orphan, f"孤立标题块={orphan}")

    # ---------- E. 首行完整性 ----------
    print("\n[E] 截断不产生半行")
    orig_lines = set(_first_lines(blocks))
    # 块首行可能来自重叠尾巴（也是原始行）或表头补行（也是原始行）
    all_lines = {l for _, b in blocks for l in b.split("\n")}
    bad_first = [i for i, t in enumerate(texts)
                 if t.split("\n", 1)[0] not in all_lines]
    chk("所有块首行都是原始行", not bad_first, f"异常块={bad_first}")

    # ---------- F. 版本与真实文件冒烟 ----------
    print("\n[F] 结构版本与真实文件冒烟")
    chk("CHUNK_SCHEMA_VERSION 已升到 4", CHUNK_SCHEMA_VERSION == 4,
        f"version={CHUNK_SCHEMA_VERSION}")
    if os.path.isfile(REAL_DOCX):
        real = parse_docx("d_1088aa227b.docx", open(REAL_DOCX, "rb").read())
        rc = chunk_blocks(real, 500, 100)
        rtexts = [c["text"] for c in rc]
        rflat = {l for _, b in real for l in b.split("\n")}
        chk("真实文件：块数合理", 8 <= len(rc) <= 24, f"n={len(rc)}")
        chk("真实文件：无碎块",
            all(len(t) >= MIN_CHUNK_CHARS for t in rtexts),
            f"最短={min(len(t) for t in rtexts)}")
        chk("真实文件：无数字中部残片",
            not any(l.startswith("06 |") for t in rtexts for l in t.split("\n")))
        chk("真实文件：首行都是原始行",
            all(t.split("\n", 1)[0] in rflat for t in rtexts))
        # LPR 表数据行所在块必须带列头
        lpr_hdr = next((t for loc, t in real if loc.startswith("表 2 第 1 行")
                        and t.startswith("公布时点")), "")
        bad = [c["text"][:40] for c in rc
               if any(l.startswith("2025/") and "| 2.9" in l for l in c["text"].split("\n"))
               and "3M" not in c["text"]]
        chk("真实文件：LPR 续块带表头", not bad, f"缺表头={bad}")
        # 表格不脱离所属标题：含 LPR 数据行的块，其章节路径应指向「二、」
        sec_bad = [c["section"][:40] for c in rc
                   if any(l.startswith("2025/") and "| 2.9" in l for l in c["text"].split("\n"))
                   and "贷款市场报价利率" not in (c.get("path") or "")]
        chk("真实文件：LPR 表块章节路径含「二、贷款市场报价利率」", not sec_bad,
            f"错位={sec_bad}")
    else:
        print("  [SKIP] 真实文件不存在，跳过冒烟")

    print(f"\n结果：OK={OK} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
