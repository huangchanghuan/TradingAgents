# 每日 33 ETF 跑批 + 三站发布（Windows 版自动化提示词模板）

> 供 WorkBuddy 定时任务使用。创建自动化时，请把下文的 `{TRADINGAGENTS_DIR}` 和 `{REPORT_SITE_DIR}` 替换为实际绝对路径后再作为自动化提示词。

---

在 {TRADINGAGENTS_DIR} 项目下，对以下 33 只 ETF 做全池多智能体分析（4 路并发），分析日用当天日期（PowerShell `Get-Date -Format yyyy-MM-dd`）：

516020.SS 512400.SS 588710.SS 588160.SS 513750.SS 513090.SS 512880.SS 516860.SS 588830.SS 159840.SZ 159857.SZ 159611.SZ 516630.SS 159326.SZ 563230.SS 517520.SS 159707.SZ 159208.SZ 159949.SZ 512690.SS 513970.SS 512100.SS 510500.SS 510300.SS 512710.SS 159928.SZ 159570.SZ 159688.SZ 588200.SS 159732.SZ 159559.SZ 512980.SS 159869.SZ

执行方式：

1. 先探测本地网关：GET `http://127.0.0.1:3425/v1/models`，返回非 200 则如实报告并停止（不要发布站点；提示用户启动 WorkBuddy 桌面端）。
2. 运行 `pwsh -File daily_run.ps1` 跑批（run_in_background 拉起，用 TaskOutput 长阻塞等到全部完成，不要中途放弃；预计约 1 小时；日志与状态文件在 `.workbuddy\daily_run\`，status 文件名 `status_<分析日>.txt`）。
3. 完成后统计 status 文件：对 exit=1 的代码逐只用 2 并发重跑一次（`run.bat <代码> <分析日> market,news,fundamentals`）；若某只连续两次在同一阶段被「商汤小浣熊内容安全 400」拒绝，判定为确定性触发，放弃该只，不再重试。
4. 最终成功 ≥30 只才发布。发布顺序：
   a. 在 {REPORT_SITE_DIR} 下用 venv 的 python 跑 `sync.py`（增量同步，约 15-25 分钟，阻塞等待不要中断）
   b. `wrangler deploy -c wrangler.jsonc`（主站 Cloudflare）
   c. 用 workbuddy_sites_deploy 工具发布备站：directory 指向 {REPORT_SITE_DIR}\site 子目录，domainPrefix 用 tradingagents-stock，userAskedToPublish=true
   d. git add -A && git commit -m "daily sync <分析日>" && git push（GitHub Pages 由 workflow 自动更新）
5. 验证：主站 `https://tradingagents.sunstar.workers.dev` 的 `/` 与 `/data/reports.json` 返回 200。若 curl 全部 000 而备站（sharecode 链接）正常，属本机对 *.workers.dev 的 DNS 污染/SNI 阻断，部署本身已成功，不必反复重试。
6. 最终汇报：成功/失败数量、失败代码列表、评级分布（从 site/data/reports.json 取当日条目按 rating 统计）、三站部署状态与版本号。失败超过 3 只不要发布站点，如实报告失败明细。

其他约定：
- 东财接口有节流，不要把并发提高到 4 以上
- 网关恢复类瞬时故障（502 DNS ESERVFAIL）重跑即可恢复；集中失败时先测网关再重试
- 报告输出在 `%USERPROFILE%\.tradingagents\logs\reports\<代码>_<日期戳>\`，完整产物标志是 `5_portfolio\decision.md` 存在
