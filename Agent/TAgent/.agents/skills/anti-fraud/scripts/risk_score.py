#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
涉诈风险打分器 (anti-fraud risk score)

对疑似场景按标准化信号清单打分，输出风险分值与等级。
信号定义与 references/red-flags.md 的「高危信号清单」一致。

用法:
    # 列出全部信号
    python3 risk_score.py --list

    # 指定命中的信号编号
    python3 risk_score.py --signals S01,S03,S11

    # 通过 JSON 指定 (true/false)
    python3 risk_score.py --json '{"S01": true, "S05": false}'

    # 交互式逐项确认
    python3 risk_score.py

    # 从 stdin 读取信号编号（逗号或空格分隔）
    echo "S01 S03" | python3 risk_score.py --stdin

输出: 命中项明细 + 总分 + 等级 + 建议动作，纯文本表格。
"""

import argparse
import json
import sys

SIGNALS = [
    ("S01", "要求转账到「安全账户 / 监管账户 / 验资账户」", 30),
    ("S02", "索要短信验证码 / 支付密码 / 银行卡密码", 30),
    ("S03", "要求屏幕共享 / 远程控制 / 安装无障碍插件", 25),
    ("S04", "要求下载非官方应用市场的 App、安装描述文件", 20),
    ("S05", "自称官方但联系方式非官方渠道", 20),
    ("S06", "先小额返利，尝到甜头后诱导加大投入", 25),
    ("S07", "承诺保本高收益 / 内幕消息", 20),
    ("S08", "要求向多个不同个人账户分散转账 / 频繁换收款账户", 25),
    ("S09", "要求现金、黄金、购物卡、USDT 线下交割", 25),
    ("S10", "诱导网贷借款后再转出", 25),
    ("S11", "制造紧迫感：限时、逾期冻结、追责、影响征信", 15),
    ("S12", "要求保密，不得告知家人朋友", 15),
    ("S13", "陌生链接 / 二维码要求填写银行卡、身份证、验证码", 20),
    ("S14", "诱导绕过平台私下交易 / 私下转账", 15),
    ("S15", "拒绝视频通话、拒绝见面、无法提供可核验工号", 10),
    ("S16", "网恋 / 陌生导师主动建立信任后引导投资", 20),
    ("S17", "要求飞行模式、换手机、WiFi 通话（规避预警）", 20),
    ("S18", "「百万保障 / 保险到期 / 会员扣费」并要求操作资金", 25),
]

# 命中任意一项即直接判定为诈骗（一票否决）
DEADLY = {"S01", "S02", "S03"}

LEVELS = [
    (70, "极高危", "是诈骗", "立即中止一切操作，进入止损流程（停止转账 → 冻结账户 → 110 紧急止付）"),
    (50, "高危", "高度疑似", "停止转账，按诈骗处置；通过官方渠道独立核实"),
    (30, "中危", "存疑", "暂停操作，自行通过官方渠道核实后再决定"),
    (15, "低危", "可疑", "保持警惕，不泄露信息、不点链接、不装 App"),
    (0, "未见明显风险", "暂未识别到诈骗特征", "常规提醒，保持基本防范意识"),
]

SIGNAL_MAP = {code: (desc, score) for code, desc, score in SIGNALS}


def level_of(score: int):
    for threshold, name, verdict, action in LEVELS:
        if score >= threshold:
            return name, verdict, action
    return LEVELS[-1][1], LEVELS[-1][2], LEVELS[-1][3]


def render(hits):
    total = 0
    lines = []
    for code in hits:
        desc, score = SIGNAL_MAP[code]
        total += score
        flag = " [一票否决]" if code in DEADLY else ""
        lines.append("  {:<5} {:<3}  {}{}".format(code, "+%d" % score, desc, flag))

    name, verdict, action = level_of(total)
    deadly_hit = bool(set(hits) & DEADLY)
    if deadly_hit:
        name, verdict, action = "极高危", "是诈骗", "立即中止一切操作，进入止损流程（停止转账 → 冻结账户 → 110 紧急止付）"
        total = max(total, 70)

    print("=" * 68)
    print("涉诈风险评分结果")
    print("=" * 68)
    if lines:
        print("命中信号：")
        print("\n".join(lines))
    else:
        print("命中信号：无")
    print("-" * 68)
    print("总分：{}".format(total))
    print("等级：{}（{}）".format(name, verdict))
    if deadly_hit:
        print("判定：命中一票否决项，直接判定为诈骗。")
    print("-" * 68)
    print("建议动作：{}".format(action))
    print("=" * 68)
    print("注：本结果由规则化评分得出，用于辅助判断，不构成公安机关结论或法律意见。")
    return 1 if (total >= 50 or deadly_hit) else 0


def parse_signals(raw: str):
    parts = [p.strip().upper() for p in raw.replace(",", " ").split()]
    unknown = [p for p in parts if p not in SIGNAL_MAP]
    if unknown:
        sys.stderr.write("未知信号编号：{}\n".format("、".join(unknown)))
        sys.exit(2)
    return parts


def interactive():
    hits = []
    print("逐项确认以下信号是否命中（y/N，直接回车跳过）：\n")
    for code, desc, score in SIGNALS:
        try:
            ans = input("  {:<5} (+{:<2}) {}  → ".format(code, score, desc)).strip().lower()
        except EOFError:
            break
        if ans in ("y", "yes", "1", "是"):
            hits.append(code)
    print()
    return hits


def main():
    ap = argparse.ArgumentParser(description="涉诈风险打分器")
    ap.add_argument("--signals", help="命中的信号编号，逗号或空格分隔，如 S01,S03")
    ap.add_argument("--json", help='JSON 形式指定，如 \'{"S01": true, "S05": false}\'')
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读取信号编号")
    ap.add_argument("--list", action="store_true", help="列出全部信号清单")
    args = ap.parse_args()

    if args.list:
        print("{:<6}{:<6}{}".format("编号", "分值", "信号"))
        print("-" * 68)
        for code, desc, score in SIGNALS:
            print("{:<6}{:<6}{}".format(code, score, desc))
        print("-" * 68)
        print("一票否决项（命中即判定为诈骗）：" + "、".join(sorted(DEADLY)))
        return 0

    if args.json:
        try:
            data = json.loads(args.json)
        except json.JSONDecodeError as e:
            sys.stderr.write("JSON 解析失败：{}\n".format(e))
            return 2
        hits = [k.upper() for k, v in data.items() if v and k.upper() in SIGNAL_MAP]
    elif args.signals:
        hits = parse_signals(args.signals)
    elif args.stdin:
        hits = parse_signals(sys.stdin.read())
    else:
        if sys.stdin.isatty():
            hits = interactive()
        else:
            ap.print_help()
            return 2

    # 去重并保持清单顺序
    order = [c for c, _, _ in SIGNALS]
    hits = [c for c in order if c in set(hits)]
    return render(hits)


if __name__ == "__main__":
    sys.exit(main())
