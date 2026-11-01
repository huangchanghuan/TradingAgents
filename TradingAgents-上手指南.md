# TradingAgents 上手指南（本机已装好版）

> 仓库：https://github.com/TauricResearch/TradingAgents
> 版本：v0.7.1（2026-10）｜论文：[arXiv 2412.20138](https://arxiv.org/abs/2412.20138)
> 本机安装位置：`/home/sun/WorkBuddy/2026-10-31-22-23-52/TradingAgents`（源码）+ `.venv`（虚拟环境）

---

## 一、它到底是什么

一句话：**把"一家小型投研团队"拆成 7 个 LLM 智能体，让它们按流程吵完一架，最后给你一份带买卖评级的完整研报。**

输入：一个股票代码 + 一个分析日期。
输出：4 份分析师报告 + 多空辩论纪要 + 交易员方案 + 风险评估 + 组合经理最终决策（BUY / OVERWEIGHT / HOLD / UNDERWEIGHT / SELL + 理由），外加一份 `complete_report.html`。

它**不是**：不是量化策略、不是稳赚的信号源、不是回测平台（虽然有简单回测）。官方自己写明：仅用于研究，不构成投资建议。

## 二、跑一次会发生什么（流程）

```
分析师团队（并行，各带工具）           研究员团队              决策层
├─ 技术面  MACD/RSI/均线              多空辩论 N 轮           交易员：给方案（方向/理由）
├─ 基本面  SEC 财报、内部人交易   →   ├─ 看多研究员      →    风险团队：激进/中性/保守 三方讨论
├─ 新闻    个股+宏观新闻              └─ 看空研究员            组合经理：拍板 5 档评级
└─ 情绪    Reddit/StockTwits
                     ↓
             记忆日志（~/.tradingagents/memory/trading_memory.md）
       每次跑完追加决策，下次跑先"结算"上次的结果，算出真实收益与 alpha，写反思
```

关键设计（这是它比"一句话问 GPT"值钱的地方）：

| 机制 | 作用 |
|---|---|
| **Point-in-time 数据** | 分析 2026-03-01 时，只读那天之前**已公开**的财报（按 SEC 申报日期过滤），避免"用未来数据回测"的作弊 |
| **多空对抗辩论** | 强制生成反面观点，防止模型一路看多 |
| **记忆 + 反思** | 决策会被后续运行打分（持有 5 个交易日的收益 vs 基准 alpha），经验回灌给下一次 |
| **双模型分层** | quick 模型干杂活（分析师/辩论），deep 模型做终审（经理），省钱又保质量 |

## 三、安装（本机已完成，下面是复现命令）

要求 Python ≥ 3.11。

```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
python3 -m venv .venv           # 或 conda create -n tradingagents python=3.13
source .venv/bin/activate
pip install .                   # 注意：是 pip install .，不是 pip install tradingagents
```

已验证：安装耗时约 3 分钟，装完 80+ 依赖（langgraph / langchain / yfinance / stockstats 等）。

验证：

```bash
cd /home/sun/WorkBuddy/2026-10-31-22-23-52
.venv/bin/tradingagents --help
```

Docker 方式（想隔离环境时用）：

```bash
cp .env.example .env      # 填 key
docker compose run --rm tradingagents
```

## 四、配一个模型（唯一必须做的事）

没有 API Key 就跑不了 —— 整个流程全靠 LLM 驱动。选一家填进 `.env`：

```bash
cd /home/sun/WorkBuddy/2026-10-31-22-23-52/TradingAgents
cp .env.example .env
```

**国内网络友好、价格便宜的三个选择：**

| 提供商 | 环境变量 | 推荐 quick 模型 | 推荐 deep 模型 | 备注 |
|---|---|---|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` | `deepseek-v4-pro` | 最省，工具调用支持好 |
| 通义千问 | `DASHSCOPE_CN_API_KEY` | 见模型目录 | 见模型目录 | 走 dashscope.aliyuncs.com |
| 智谱 GLM | `ZHIPU_CN_API_KEY` | GLM 系列 | GLM 系列 | 走 bigmodel.cn |
| Kimi | `MOONSHOT_API_KEY` | — | — | 长上下文友好 |
| OpenAI / Claude / Gemini | `OPENAI_API_KEY` 等 | `gpt-6-luna` | `gpt-6-sol` | 效果最好，但要走代理且贵 |

`.env` 里除了 key，建议同时定死下面几行，省掉每次交互式提问：

```ini
TRADINGAGENTS_LLM_PROVIDER=deepseek
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-v4-pro
TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese
TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
TRADINGAGENTS_MAX_RISK_ROUNDS=1
SEC_EDGAR_USER_AGENT=your_name your@email.com   # 抓美股财报用，填你自己邮箱
```

> `OUTPUT_LANGUAGE=Chinese` 只影响最终报告语言，智能体内部辩论仍是英文（推理质量更好）。

## 五、第一次运行

**方式 A：交互式（推荐第一次）**

```bash
cd /home/sun/WorkBuddy/2026-10-31-22-23-52
.venv/bin/tradingagents
```

会依次问你：股票代码 → 分析日期 → 分析师（选几个）→ 提供商 → 研究深度 → 辩论轮数。上次的选择会成为下次默认值。

**方式 B：一行命令跑完（脚本/定时任务用）**

```bash
cd /home/sun/WorkBuddy/2026-10-31-22-23-52
.venv/bin/tradingagents \
  --ticker NVDA --date 2026-10-30 \
  --analysts market,news,fundamentals \
  --save --html --no-show
```

**A 股示例**（沪深后缀是 `.SS` / `.SZ`）：

```bash
.venv/bin/tradingagents --ticker 600519.SS --date 2026-10-30 --analysts market,fundamentals --save --html --no-show
```

支持的代码格式：美股 `AAPL`、港股 `0700.HK`、日本 `7203.T`、伦敦 `AZN.L`、A 股 `600519.SS` / `000001.SZ`、加密 `BTC-USD`。

**输出在哪**

```
~/.tradingagents/logs/reports/NVDA_20261031_2230/
├── complete_report.html      ← 一份完整网页报告，直接浏览器打开
├── complete_report.md
├── analyst/                  ← 技术面/基本面/新闻/情绪 各自报告
├── research/                 ← 多空辩论纪要
├── trader/                   ← 交易员方案
├── risk/                     ← 三方风险讨论
└── manager/                  ← 组合经理最终决策
```

**成本与耗时预期**：默认 4 个分析师 + 1 轮辩论 + 1 轮风险讨论，一次大约 5–15 分钟、几十万 token。用 deepseek 这类便宜模型大概几毛到一两块人民币；用 GPT-6 / Claude Opus 这类会到十几块。想更快更省：少选分析师（`--analysts market,fundamentals`）、辩论轮数设 1、`max_tool_rounds` 调小到 8–10。

## 六、A 股数据增强：内置 `a_stock` 数据源（本机已改造）

原版的 A 股数据来自 yfinance，口径和东财/同花顺差得多。这台机器上的源码已经加了一个 `a_stock` 数据源，直接接内地公开接口，**不需要任何 key**。

**它现在负责什么**

| 数据 | 来源 | 特点 |
|---|---|---|
| 日/周/月 K 线 | 腾讯 `ifzq.gtimg.cn` | 前复权，按回测日重锚（看到的是当时的价格水位） |
| 实时快照 | 腾讯 `qt.gtimg.cn` | PE(TTM)/PB/市值/涨跌停价 |
| 三大报表 | 新浪财经 | 按公告口径、人民币、企业会计准则 |
| 公司概况 | 东方财富 | 行业、板块、总股本、上市日 |
| 个股新闻 | 东方财富搜索 | 中文媒体原文，带时间窗过滤 |
| 宏观快讯 | 财联社 + 东财 7×24 | 用于新闻分析师的宏观视角 |
| 股东增减持 | 东方财富 | 按公告日过滤 |

**默认数据源链已改成**（`tradingagents/default_config.py`）：

```python
"core_stock_apis":     "a_stock,yfinance",
"technical_indicators":"a_stock,yfinance",
"fundamental_data":    "a_stock,sec_edgar,yfinance",
"news_data":           "a_stock,yfinance",
```

`a_stock` 排在链首，但对**非 A 股代码会直接抛 `VendorUnavailableError` 让位**（不发网络请求），所以 `NVDA`、`0700.HK`、`BTC-USD` 仍然是原来的 yfinance / SEC EDGAR 链路。这一点有测试守着：`tests/test_a_stock_vendor.py::test_the_default_chain_keeps_us_tickers_on_yahoo`。

**时点正确性（backtest 必需）**

历史回测最怕"偷看未来"。这个数据源做了三道闸：

1. **财报按法定披露期限过滤**：一季报/三季报 +30 天、半年报 +62 天、年报 +120 天。跑 2025-05-10 的分析，只给你 2025-03-31 及更早的报告期。
2. **实时估值只在"今天"给**：PE/PB/市值这类数字来自腾讯的实时行情接口，它只会回答"现在是多少"，没有历史值可查。跑 2026-10-30 的分析却喂今天的 PE，等于让模型用未来的估值做判断（回测会虚高），所以这种情况下**直接不给**，并在报告里写明为什么。真实返回值长这样：

   ```
   Profile fundamentals are withheld for this date. This vendor serves only
   present-day values with no historical vintage: market cap, valuation
   multiples, the 52-week range and TTM income move with today's quote, and
   even the name, sector and industry reflect today rather than 2026-10-30.
   ```

   注意它连"行业""公司名"一起隐去——公司会改名、会被重新分类，今天的行业标签未必等于当天的。这不是缺数据，是**宁可不给也不编**：区别在于模型收到"此处无数据"会谨慎，收到一个错数字会自信地算错。想要历史估值，得另接有日期存档的接口，这个数据源没有。
3. **复权因子按分析日重锚**：不会拿今天的复权基准去套 2023 年的价格。

**怎么关掉 / 换回去**

想恢复原版行为，改 `TradingAgents/.env` 或用代码覆盖即可：

```python
config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "sec_edgar,yfinance",
    "news_data": "yfinance",
}
```

**支持范围：按"要什么"分两层，不是按"是不是股票"**

这一点容易搞混，所以拆开说。同一个代码，取行情能拿到，取财报拿不到，是正常的：

| 你要的东西 | 股票 | ETF / LOF | 可转债 | 指数 |
|---|---|---|---|---|
| 日/周/月 K 线、技术指标 | ✅ | ✅ | ✅ | 深市 ✅ / 沪市 ❌ |
| 实时快照（价格、涨跌幅） | ✅ | ✅ | ✅ | 同上 |
| 个股新闻 | ✅ | ✅ | ✅ | — |
| 公司财报（三大表） | ✅ | ❌ 本来就没有 | ❌ | ❌ |
| 公司概况、股东增减持 | ✅ | ❌ | ❌ | ❌ |

实测过的例子：`510300.SS`（沪深300ETF）能正常取到 K 线（`2026-09-10, 4.617, 4.64, 4.599, 4.617, 438854100`），RSI 也能算；但请求财报时会主动说"这是基金，没有公司报表"并把请求让给 yfinance——**不会凭空造一张利润表出来**。

`510300.SS` / `159915.SZ` / `588000.SS` 这类 ETF 代码可以直接用，不需要特殊写法。

**沪市指数（`000300.SS`）被刻意拒绝**：`000` 开头沪深共用（`000001` 既是平安银行也是上证指数），判错会静默返回另一只票的数据，比返空危险，所以不猜。深市指数（`399001.SZ`）没有歧义，能取行情。要分析指数本身的走势，建议直接用对应 ETF。

社交情绪（Reddit / StockTwits）依然只有美股数据 —— 这块没有补。

## 七、当代码库用（Python API）

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["deep_think_llm"] = "deepseek-v4-pro"
config["quick_think_llm"] = "deepseek-v4-flash"
config["max_debate_rounds"] = 1
config["output_language"] = "Chinese"

ta = TradingAgentsGraph(debug=True, config=config)
state, decision = ta.propagate("NVDA", "2026-10-30")   # 返回全流程状态 + 最终决策
print(decision)
ta.save_reports(state, "NVDA")                          # 落盘成上面的报告树
```

传入你自己的持仓，让风控和交易员按真实仓位定规模：

```python
from tradingagents.portfolio import PortfolioContext
portfolio = PortfolioContext.model_validate({
    "cash": 25000.0, "currency": "USD",
    "positions": [{"ticker": "NVDA", "quantity": 120, "average_price": 150.0}],
})
_, decision = ta.propagate("NVDA", "2026-10-30", portfolio=portfolio)
```

## 八、进阶命令

```bash
tradingagents backtest NVDA,AAPL --start 2026-06-01 --end 2026-08-01 --every 7
# 在"股票 × 日期"网格上跑，按评级分组统计相对基准的已实现 alpha；用 --every 控制间隔天数

tradingagents --checkpoint          # 开启断点续跑，崩了从最后一节点继续
tradingagents --clear-checkpoints   # 清空断点重跑
tradingagents --portfolio my_book.json
tradingagents login chatgpt         # 用 ChatGPT Plus/Pro 订阅额度，不用 API key
```

## 九、踩坑清单（重要）

1. **结果不可复现**。同一股票同一天跑两次，结论可能不同 —— 这是 LLM 采样的必然。想稳定一点：`TRADINGAGENTS_TEMPERATURE=0`，但推理型模型基本忽略 temperature。
2. **yfinance 会被限流**。连着跑多只票容易 429，加延时或换 `alpha_vantage`（需 key）。
3. **A 股数据已换成本地数据源，但"能取行情"不等于"有基本面"**。行情/指标/新闻对股票、ETF、可转债都通；公司财报和股东数据只有股票有（ETF 和转债结构上就没有）。估值为实时快照，回测历史日期时会主动隐去而不是编一个——详见第六节。
4. **社交情绪基本是美股的**。Reddit / StockTwits 对 A 股、港股几乎无信息，情绪分析师在非美股上价值有限 —— 这块 `a_stock` 没有补。
5. **别把它当信号源**。它最实际的用法：**当作一个会自己吵架的研报生成器 + 反面意见清单**，用来发现自己看漏的风险点，而不是直接照抄评级。
6. **首次运行会联网检查新版本**，CI/无网环境设 `TRADINGAGENTS_NO_UPDATE_CHECK=1`。

---

## 附：本机现状

- 源码：`/home/sun/WorkBuddy/2026-10-31-22-23-52/TradingAgents`（已做 A 股数据源改造，见第六节）
- 虚拟环境：`/home/sun/WorkBuddy/2026-10-31-22-23-52/.venv`（Python 3.13，tradingagents 0.7.1 已装好）
- 行情数据链路：已验证可正常拉取（yfinance 通；A 股走腾讯/新浪/东财，也已逐接口实测）
- 测试：`../.venv/bin/python -m pytest tests -q` → 1432 passed / 1 skipped
- 待办：**填 API Key**（`TradingAgents/.env`），然后就能跑第一次分析
