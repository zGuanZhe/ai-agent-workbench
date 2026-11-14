#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_demo_fixtures.py
在 work-super-agent/workspace 下生成真实可测的办公办公测试夹具：
1. 科室底册.xlsx（包含5个应交科室：财务科、人事科、信息中心、政工科、后勤处）
2. 模拟微信收件箱中的3份已交材料（含 Word 摸底表与 Excel 摸底表）：
   - 财务科_张三_专项经费摸底表.docx（数据完全合规：设备12 + 培训3.5 + 运维4.5 = 20.0万元）
   - 人事科_李四_专项经费摸底表.xlsx（数据完全合规：设备5 + 培训8 + 运维2 = 15.0万元）
   - 信息中心_王五_专项经费摸底表.docx（故意构造勾稽错误与手机号位数错误：设备18 + 培训2.5 + 运维8.0 = 28.5万元，但总计填了 30.0万元，差额1.5万元；电话少1位）
   （政工科、后勤处未交，用于验证未交名单比对与微信群催办话术生成）
3. 空白汇总母表模板：2026年11月专项经费汇总母表_模板.xlsx
4. 公文排版草稿：2026年度工作总结草稿.md
"""

from pathlib import Path
from docx import Document
import openpyxl


def main():
    base = Path(__file__).resolve().parent.parent / "workspace"
    inbox_dir = base / "00_模拟微信收件缓存"
    dir_01 = base / "01_微信收回原件"
    dir_03 = base / "03_汇总与成稿输出"
    dir_04 = base / "04_单位历年材料库"

    for d in (inbox_dir, dir_01, dir_03, dir_04):
        d.mkdir(parents=True, exist_ok=True)

    # 1. 创建《科室底册.xlsx》
    wb_roster = openpyxl.Workbook()
    ws_r = wb_roster.active
    ws_r.title = "应交科室底册"
    ws_r.append(["科室名称", "联络员", "联系电话"])
    roster_rows = [
        ["财务科", "张三", "13800138001"],
        ["人事科", "李四", "13900139002"],
        ["信息中心", "王五", "13700137003"],
        ["政工科", "赵六", "13600136004"],
        ["后勤处", "孙七", "13500135005"],
    ]
    for r in roster_rows:
        ws_r.append(r)
    roster_path = base / "科室底册.xlsx"
    wb_roster.save(str(roster_path))

    # 2. 创建《2026年11月专项经费汇总母表_模板.xlsx》
    wb_tpl = openpyxl.Workbook()
    ws_t = wb_tpl.active
    ws_t.title = "2026年11月专项经费汇总"
    ws_t.append([
        "序号",
        "科室名称",
        "联络员",
        "联系电话",
        "设备采购费(万元)",
        "业务培训费(万元)",
        "系统运维费(万元)",
        "经费总计(万元)",
        "备注",
    ])
    tpl_path = base / "2026年11月专项经费汇总母表_模板.xlsx"
    wb_tpl.save(str(tpl_path))

    # 3. 创建子表1：财务科_张三_专项经费摸底表.docx（键值对登记表型 Word 表格）
    doc1 = Document()
    doc1.add_heading("2026年度专项经费摸底申报表", level=1)
    t1 = doc1.add_table(rows=5, cols=4)
    kv_rows_1 = [
        ("填报科室", "财务科", "联络员", "张三"),
        ("联系电话", "13800138001", "设备采购费(万元)", "12.0"),
        ("业务培训费(万元)", "3.5", "系统运维费(万元)", "4.5"),
        ("经费总计(万元)", "20.0", "备注", "含金审工程三期终端升级"),
        ("填报日期", "2026-11-09", "审核状态", "已初审"),
    ]
    for r_i, row_tuple in enumerate(kv_rows_1):
        for c_i, val in enumerate(row_tuple):
            t1.cell(r_i, c_i).text = val
    doc1.save(str(inbox_dir / "财务科_张三_专项经费摸底表.docx"))

    # 4. 创建子表2：人事科_李四_专项经费摸底表.xlsx（行表型 Excel）
    wb_sub2 = openpyxl.Workbook()
    ws_s2 = wb_sub2.active
    ws_s2.append([
        "科室名称",
        "联络员",
        "联系电话",
        "设备采购费(万元)",
        "业务培训费(万元)",
        "系统运维费(万元)",
        "经费总计(万元)",
        "备注",
    ])
    ws_s2.append(["人事科", "李四", "13900139002", 5.0, 8.0, 2.0, 15.0, "干部全员数字素养专题培训"])
    wb_sub2.save(str(inbox_dir / "人事科_李四_专项经费摸底表.xlsx"))

    # 5. 创建子表3：信息中心_王五_专项经费摸底表.docx（含勾稽不符：分项相加28.5 vs 总计30.0，差1.5万；且电话仅10位）
    doc3 = Document()
    doc3.add_heading("2026年度专项经费摸底申报表", level=1)
    t3 = doc3.add_table(rows=4, cols=4)
    kv_rows_3 = [
        ("填报科室", "信息中心", "联络员", "王五"),
        ("联系电话", "1370013700", "设备采购费(万元)", "18.0"),
        ("业务培训费(万元)", "2.5", "系统运维费(万元)", "8.0"),
        ("经费总计(万元)", "30.0", "备注", "办公云算力扩容及安全加固"),
    ]
    for r_i, row_tuple in enumerate(kv_rows_3):
        for c_i, val in enumerate(row_tuple):
            t3.cell(r_i, c_i).text = val
    doc3.save(str(inbox_dir / "信息中心_王五_专项经费摸底表.docx"))

    # 6. 创建公文排版测试草稿《2026年度工作总结草稿.md》
    draft_md = """# 关于2026年度办公数字化转型与专项经费摸底情况的报告
局党组：
今年以来，全局紧扣数字政府建设主线，扎实推进机关办公自动化与数据底座赋能，各项重点工作取得阶段性成效。现将有关情况报告如下：
一、聚焦主责主业，在办公效能提升上实现新突破
（一）全面建成公文与报表自动化流水线
1.大幅压减基层填表负担。通过部署智能收件与跨表校验引擎，将以往耗时半天的多科室摸底汇总压缩至秒级完成，实现数据100%溯源校验。
2.严格落实国家公文格式标准。全面对标《党政机关公文格式》（GB/T 9704-2012），实现版头、标题、三级正文层级与页码的标准化一键输出。
（二）强化经费预算刚性约束与勾稽核查
1.精准识别预算填报异常。在11月专项经费摸底中，自动完成财务科、人事科、信息中心等科室申报表合并，并精准拦截分项与总计勾稽差额1.5万元。
二、下一步工作计划
（一）健全常态化数据核验与督办机制
持续完善各处室台账联审机制，确保各项财政专项资金使用规范透明、绩效可查。
局数字化转型专班
2026年11月9日
"""
    (dir_04 / "2026年度工作总结草稿.md").write_text(draft_md.strip(), encoding="utf-8")
    print("Demo fixtures generated successfully!")


if __name__ == "__main__":
    main()
