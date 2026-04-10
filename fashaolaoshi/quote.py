"""
腾讯财经实时行情查询
接口: https://qt.gtimg.cn/q=code1,code2,...
编码: GB18030
"""
import sys
import time
import os
import requests
from typing import Optional

# PowerShell 终端默认编码与 GB18030 不兼容，强设 UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8-sig', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8-sig', errors='replace')
except Exception:
    pass

BASE_URL = "https://qt.gtimg.cn/q="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.qq.com",
}

# 美股前缀映射（腾讯接口要求 us 前缀）
US_STOCKS = {"aapl", "msft", "googl", "googl", "amzn", "meta", "tsla", "nvda", "nvdq", "ibm"}

def normalize_code(code: str) -> str:
    """标准化股票代码为腾讯接口格式"""
    c = code.strip().lower()
    if c.startswith("us"):
        return c  # 已是 us 前缀
    if c.upper() in US_STOCKS or (len(c) >= 3 and c.isalpha() and not c.startswith(("sh", "sz", "hk"))):
        return "us" + c.upper()
    return code.strip()


def fetch(codes: list[str]) -> list[dict]:
    """批量获取行情，返回结构化数据列表"""
    results = []
    # 标准化代码（美股加 us 前缀）
    normalized = [normalize_code(c) for c in codes]
    # 每批最多 100 个，间隔 110ms 避免封 IP
    batch_size = 100
    for i in range(0, len(normalized), batch_size):
        batch = normalized[i : i + batch_size]
        url = BASE_URL + ",".join(batch)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "gb18030"
            lines = r.text.strip().split("\n")
            for line in lines:
                if not line.strip():
                    continue
                # 格式: v_sh000001="fields..."
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                code = key.replace("v_", "")
                fields = value.strip('"').split("~")
                if len(fields) < 35:
                    results.append({"code": code, "error": "数据不足"})
                    continue
                try:
                    # 时间格式: yyyymmddHHMMSS -> date + time
                    # A股格式: "20260408161415", 港股格式: "2026/04/08 16:08:20"
                    dt_str = fields[30] if len(fields) > 30 else ""
                    if "/" in dt_str:
                        # 港股格式
                        date_str, time_str = dt_str.replace("/", "-"), ""
                    else:
                        # A股格式
                        date_str = dt_str[:8] if dt_str else ""
                        time_part = dt_str[8:14] if len(dt_str) > 8 else ""
                        if len(time_part) == 6:
                            time_str = f"{time_part[:2]}:{time_part[2:4]}:{time_part[4:6]}"
                        else:
                            time_str = ""
                        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if len(date_str) >= 8 else ""

                    # 关键字段索引（以实际数据校验，参考文档整体偏1位）：
                    # index 3=最新价, 4=昨收, 5=开盘, 6=成交量
                    # index 31=涨跌额, 32=涨跌幅(%), 33=最高价, 34=最低价
                    change_val = float(fields[31]) if len(fields) > 31 and fields[31] else 0
                    change_pct_val = float(fields[32]) if len(fields) > 32 and fields[32] else 0
                    high_val = float(fields[33]) if len(fields) > 33 and fields[33] else 0
                    low_val = float(fields[34]) if len(fields) > 34 and fields[34] else 0

                    # 成交额：index 35 = "price/vol/turnover(元)"
                    turnover_yuan = ""
                    if len(fields) > 35:
                        parts = fields[35].split("/")
                        if len(parts) >= 3:
                            turnover_yuan = parts[2]
                    elif len(fields) > 37:
                        turnover_yuan = str(float(fields[37]) * 10000) if fields[37] else ""

                    # 市值(亿)：index 45/46
                    market_cap_yi = fields[45] if len(fields) > 45 and fields[45] else ""
                    total_mcap_yi = fields[46] if len(fields) > 46 and fields[46] else ""

                    # 交易所
                    ex_id = fields[0] if len(fields) > 0 else "?"
                    if ex_id == "1":
                        exchange = "SSE"
                    elif ex_id == "51":
                        exchange = "SZSE"
                    elif ex_id == "100":
                        exchange = "HKEX"
                    else:
                        exchange = f"EX{ex_id}"

                    results.append({
                        "code": code,
                        "name": fields[2] if len(fields) > 2 else code,
                        "exchange": exchange,
                        "price": float(fields[3]) if fields[3] else 0,
                        "prev_close": float(fields[4]) if fields[4] else 0,
                        "open": float(fields[5]) if fields[5] else 0,
                        "volume_lot": int(float(fields[6])) if fields[6] else 0,
                        "high": high_val,
                        "low": low_val,
                        "change": change_val,
                        "change_pct": change_pct_val,
                        "turnover_yuan": float(turnover_yuan) if turnover_yuan else 0,
                        "pe": fields[40] if len(fields) > 40 and fields[40] else "",
                        "market_cap_yi": float(market_cap_yi) if market_cap_yi else "",
                        "date": date_str,
                        "time": time_str,
                    })
                except (ValueError, IndexError) as e:
                    results.append({"code": code, "error": str(e)})
        except requests.RequestException as e:
            print(f"[错误] 请求失败: {e}", file=sys.stderr)
            if i + batch_size < len(codes):
                time.sleep(0.2)

        # 批次间隔（避免封 IP）
        if i + batch_size < len(normalized):
            time.sleep(0.11)

    return results


def fmt_money(yuan: float) -> str:
    """成交额/市值格式化"""
    if yuan >= 1e12:
        return f"{yuan/1e12:.2f}万亿"
    elif yuan >= 1e8:
        return f"{yuan/1e8:.2f}亿"
    elif yuan >= 1e4:
        return f"{yuan/1e4:.2f}万"
    return f"{yuan:.0f}元"


def print_quote(data: dict):
    """打印单条行情"""
    if "error" in data:
        print(f"  {data['code']}: 获取失败 {data['error']}")
        return

    sign = "+" if data["change"] >= 0 else ""
    vol_str = f"{data['volume_lot']:>14,}" if data["volume_lot"] else "N/A"
    turnover_str = fmt_money(data["turnover_yuan"]) if data["turnover_yuan"] else "N/A"
    pe_str = data["pe"] if data["pe"] else "N/A"
    mc_str = f"{data['market_cap_yi']:.1f}亿" if data["market_cap_yi"] else ""

    print(f"  {data['name']}({data['code']}) [{data['exchange']}]")
    print(f"    价格: {data['price']:.3g}  {sign}{data['change']:.3g}({sign}{data['change_pct']:.2f}%)")
    print(f"    开盘: {data['open']:.3g}  最高: {data['high']:.3g}  最低: {data['low']:.3g}  昨收: {data['prev_close']:.3g}")
    print(f"    成交量: {vol_str}手  成交额: {turnover_str}  市盈率: {pe_str}  流通市值: {mc_str}")
    if data["date"] and data["time"]:
        print(f"    更新时间: {data['date']} {data['time']}")
    print()


def print_table(data: list[dict]):
    """打印汇总表格（适合多只股票对比）"""
    if not data:
        return
    # 过滤错误数据
    valid = [d for d in data if "error" not in d]
    if not valid:
        print("无可用数据")
        return

    # 标题行
    header = f"  {'代码':<12} {'名称':<10} {'最新价':>10} {'涨跌幅':>8} {'涨跌额':>8} {'开盘':>10} {'最高':>10} {'最低':>10} {'成交量(手)':>12} {'成交额':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for d in valid:
        sign = "+" if d["change"] >= 0 else ""
        vol = f"{d['volume_lot']:,}" if d["volume_lot"] else "N/A"
        turn = fmt_money(d["turnover_yuan"]) if d["turnover_yuan"] else "N/A"
        print(
            f"  {d['code']:<12} {d['name']:<10} "
            f"{d['price']:>10.3g} {sign}{d['change_pct']:>7.2f}% "
            f"{sign}{d['change']:>7.3g} {d['open']:>10.3g} "
            f"{d['high']:>10.3g} {d['low']:>10.3g} "
            f"{vol:>12} {turn:>10}"
        )
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python quote.py <code1> [code2] ...")
        print("示例: python quote.py sh000001 sz399001 sh600519")
        print("代码: sh=上海 sz=深圳 hk=港股 美股直接写代码如 AAPL")
        sys.exit(1)

    codes = sys.argv[1:]

    # --batch 模式：表格输出，适合多只股票
    if "--batch" in codes or len(codes) > 3:
        codes = [c for c in codes if c != "--batch"]
        print(f"[腾讯财经] 批量查询 {len(codes)} 只股票...\n")
        data = fetch(codes)
        print_table(data)
    else:
        print(f"[腾讯财经] 查询 {' '.join(codes)}...\n")
        data = fetch(codes)
        for d in data:
            print_quote(d)
