# eth-gas-actions-bot (AgentOnChain EP03)

用 **GitHub Actions** 免费实现 24 小时云端挂机运行的以太坊 Gas 只读告警机器人。  
配套视频：**AgentOnChain (@AgentOnChainLab) EP03**《用 GitHub Actions 免费 24 小时云端挂机运行以太坊告警机器人（Secrets 托管 + 跨轮状态持久化）》

## 核心特性

- **零服务器成本**：基于 GitHub Actions 定时巡检（`schedule`）与手动压测（`workflow_dispatch`）。
- **跨轮状态持久化**：通过仓库内 `state/alert_state.json` 记录上次告警级别、冷却时间戳、区块高度与瞬时 Gas；无状态变更时自动跳过 `git commit`，避免污染提交历史。
- **Secrets 安全托管**：`TELEGRAM_BOT_TOKEN` 与 `TELEGRAM_CHAT_ID` 仅在运行期通过环境变量注入，代码与日志零明文泄露；本地未配置 Token 时自动降级为 Dry-Run 预览模式。
- **严格只读安全红线**：仅依赖 `requests` 与 Python 标准库，只调用公共 JSON-RPC 的 `eth_blockNumber` 与 `eth_gasPrice`，**零钱包私钥、零签名、零交易**。

---

## 三步一键 Fork 部署

1. **Fork 本仓库**  
   点击右上角 `Fork`，将 `Meng-Chen201/eth-gas-actions-bot` 复制到你的 GitHub 账号下。
2. **配置 Repository Secrets**  
   进入你仓库的 `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`，添加以下两项：
   - `TELEGRAM_BOT_TOKEN`：从 `@BotFather` 获取的机器人 Token
   - `TELEGRAM_CHAT_ID`：你的个人或群组 Chat ID
3. **手动触发验证**  
   进入 `Actions` -> 选择左侧 `ETH Gas Read-Only Monitor` -> 点击 `Run workflow`（可勾选 `demo_spike` 模拟 68.50 Gwei 尖峰压测）。看到全部步骤变绿且 `state/alert_state.json` 出现 `github-actions[bot]` 的提交记录，即部署完成。

---

## 本地调试命令

```bash
pip install -r requirements.txt

# 1. 真实主网单次只读巡检（无 Token 自动进入 Dry-Run）
python src/gas_tg_bot.py --once --state-file state/alert_state.json

# 2. 模拟 68.50 Gwei 尖峰压测
python src/gas_tg_bot.py --once --demo-spike --state-file state/alert_state.json