#!/usr/bin/env python3
"""
AgentOnChain EP03: 以太坊 Gas 云端只读告警机器人 (GitHub Actions + 状态持久化)
- 严格只读公共 JSON-RPC (eth_blockNumber / eth_gasPrice)，零私钥、零签名、零交易
- 支持跨轮 JSON 状态持久化与防刷屏冷却，无状态变更时不改写文件，避免污染 Git 历史
- 阈值 (<15 / >50 Gwei) 为自定义教学示例，非以太坊官方分级
"""

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

# 公共只读以太坊主网 RPC 节点池（自动故障转移）
DEFAULT_RPC_URLS = [
    "https://ethereum-rpc.publicnode.com",
    "https://1rpc.io/eth",
    "https://eth.llamarpc.com",
]

# 自定义告警阈值（单位：Gwei，非以太坊官方标准）
LOW_GAS_THRESHOLD = 15.0
HIGH_GAS_THRESHOLD = 50.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def rpc_call(rpc_url: str, method: str, timeout: int = 10) -> str:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": []}
    resp = requests.post(rpc_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"RPC error from {rpc_url}: {data['error']}")
    return data["result"]


def fetch_mainnet_gas(custom_rpc: Optional[str] = None) -> Tuple[int, float, str]:
    """轮询公共只读节点，返回 (区块高度, Gas Gwei, 命中的节点 URL)"""
    urls = [custom_rpc] if custom_rpc else DEFAULT_RPC_URLS
    last_err: Optional[Exception] = None

    for url in urls:
        try:
            block_hex = rpc_call(url, "eth_blockNumber")
            gas_hex = rpc_call(url, "eth_gasPrice")
            block_num = int(block_hex, 16)
            gas_gwei = round(int(gas_hex, 16) / 1e9, 2)
            return block_num, gas_gwei, url
        except Exception as exc:
            last_err = exc
            print(f"[WARN] RPC 节点失败 ({url}): {exc}", file=sys.stderr)

    raise RuntimeError(f"所有只读 RPC 节点均不可用，最后报错: {last_err}")


def classify_gas_level(gas_gwei: float) -> str:
    """自定义三级分类：LOW (<15) / HIGH (>50) / NORMAL (15-50)"""
    if gas_gwei < LOW_GAS_THRESHOLD:
        return "LOW"
    if gas_gwei > HIGH_GAS_THRESHOLD:
        return "HIGH"
    return "NORMAL"


def load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {
            "last_level": "INIT",
            "last_alert_ts": 0,
            "last_block": 0,
            "last_gas_gwei": 0.0,
            "updated_at_utc": "",
        }
    try:
        with state_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "last_level": data.get("last_level", "INIT"),
            "last_alert_ts": int(data.get("last_alert_ts", 0)),
            "last_block": int(data.get("last_block", 0)),
            "last_gas_gwei": float(data.get("last_gas_gwei", 0.0)),
            "updated_at_utc": str(data.get("updated_at_utc", "")),
        }
    except Exception as exc:
        print(f"[WARN] 状态文件解析失败，将重置状态: {exc}", file=sys.stderr)
        return {
            "last_level": "INIT",
            "last_alert_ts": 0,
            "last_block": 0,
            "last_gas_gwei": 0.0,
            "updated_at_utc": "",
        }


def save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def build_telegram_html(
    level: str,
    block_num: int,
    gas_gwei: float,
    source_tag: str,
    rpc_url: str,
    prev_level: str,
) -> str:
    safe_source = html.escape(source_tag)
    safe_rpc = html.escape(rpc_url)
    safe_time = html.escape(utc_now_iso())

    level_desc = {
        "LOW": f"低 Gas 区间 (低于 {LOW_GAS_THRESHOLD:.0f} Gwei)",
        "HIGH": f"高 Gas 拥堵区间 (高于 {HIGH_GAS_THRESHOLD:.0f} Gwei)",
        "NORMAL": "常规波动区间",
    }.get(level, level)

    lines = [
        f"<b>{safe_source} 以太坊 Gas 状态告警</b>",
        f"当前级别: <b>{html.escape(level)}</b> ({html.escape(level_desc)})",
        f"瞬时 Gas: <code>{gas_gwei:.2f} Gwei</code>",
        f"最新区块: <code>#{block_num}</code>",
        f"状态变化: <code>{html.escape(prev_level)} -&gt; {html.escape(level)}</code>",
        f"只读节点: <code>{safe_rpc}</code>",
        f"巡检时间: <code>{safe_time}</code>",
        "",
        "<i>说明: 阈值 (&lt;15 / &gt;50 Gwei) 为自定义演示规则，非官方分级；仅作技术监控，非投资建议 (NFA)。</i>",
    ]
    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, text_html: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    return bool(result.get("ok"))


def run_once(args: argparse.Namespace) -> None:
    state_path = Path(args.state_file)
    state = load_state(state_path)
    now_ts = int(time.time())

    block_num, gas_gwei, rpc_url = fetch_mainnet_gas(args.rpc_url)
    source_tag = "[LIVE MAINNET]"

    if args.demo_spike:
        gas_gwei = 68.50
        source_tag = "[SIMULATED SPIKE]"
        print(f"[INFO] 已启用 --demo-spike 压测模式，覆写 Gas 为 {gas_gwei:.2f} Gwei")

    current_level = classify_gas_level(gas_gwei)
    prev_level = state["last_level"]
    elapsed = now_ts - state["last_alert_ts"]

    print(
        f"[INFO] {source_tag} Block=#{block_num} | Gas={gas_gwei:.2f} Gwei | "
        f"Level={current_level} (Prev={prev_level}) | CooldownElapsed={elapsed}s/{args.cooldown}s"
    )

    # 判定逻辑：
    # 1. 仅在 LOW 或 HIGH 区间考虑发送告警
    # 2. 若级别刚发生切换，或同级别已过冷却期，则触发告警
    should_alert = False
    if current_level in ("LOW", "HIGH"):
        if current_level != prev_level or elapsed >= args.cooldown:
            should_alert = True

    state_changed = False

    if should_alert:
        msg_html = build_telegram_html(
            level=current_level,
            block_num=block_num,
            gas_gwei=gas_gwei,
            source_tag=source_tag,
            rpc_url=rpc_url,
            prev_level=prev_level,
        )
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        if token and chat_id:
            send_telegram_message(token, chat_id, msg_html)
            print("[OK] Telegram 告警已推送。")
        else:
            print("[DRY-RUN] 未检测到 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，仅打印消息预览：")
            print("-" * 50)
            print(msg_html)
            print("-" * 50)

        state.update(
            {
                "last_level": current_level,
                "last_alert_ts": now_ts,
                "last_block": block_num,
                "last_gas_gwei": gas_gwei,
                "updated_at_utc": utc_now_iso(),
            }
        )
        state_changed = True

    elif current_level != prev_level:
        # 例如从 HIGH/LOW 恢复为 NORMAL：静默更新状态，不发告警，便于下次异常重新触发
        print(f"[INFO] 状态已恢复为 {current_level}，静默更新持久化文件。")
        state.update(
            {
                "last_level": current_level,
                "last_block": block_num,
                "last_gas_gwei": gas_gwei,
                "updated_at_utc": utc_now_iso(),
            }
        )
        state_changed = True

    else:
        print("[SKIP] 级别未变化且处于冷却期（或常规区间），跳过告警与状态文件写入。")

    if state_changed:
        save_state(state_path, state)
        print(f"[OK] 状态已持久化至 {state_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="以太坊 Gas 只读告警机器人（支持 GitHub Actions 跨轮状态持久化）"
    )
    parser.add_argument(
        "--state-file",
        default="state/alert_state.json",
        help="状态持久化 JSON 路径（默认: state/alert_state.json）",
    )
    parser.add_argument(
        "--cooldown",
        type=int,
        default=1800,
        help="同级别告警防刷屏冷却秒数（默认: 1800 秒 / 30 分钟）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="单次巡检后退出（GitHub Actions 云端模式必选）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="本地常驻循环模式下的轮询间隔秒数（默认: 60 秒）",
    )
    parser.add_argument(
        "--demo-spike",
        action="store_true",
        help="模拟 68.50 Gwei 尖峰压测，验证告警链路与状态回写",
    )
    parser.add_argument(
        "--rpc-url",
        default=None,
        help="可选：指定单个只读公共 RPC URL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.once:
        run_once(args)
        return

    print(f"[INFO] 启动本地常驻巡检模式，每 {args.interval} 秒检查一次（Ctrl+C 退出）...")
    while True:
        try:
            run_once(args)
        except Exception as exc:
            print(f"[ERROR] 本轮巡检异常: {exc}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()