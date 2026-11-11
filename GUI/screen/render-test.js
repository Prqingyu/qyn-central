#!/usr/bin/env node
/*
 * render-test.js —— 屏幕壳的**渲染逻辑**测试（不用浏览器）。
 *
 * 为什么需要它：中控机截不了图（GNOME 禁 D-Bus 截图、无头 Firefox 渲染失败），
 * 而"渲染逻辑"是会出错的地方（用户 2026-10-14 就抓到过：机器关机后，
 * 数据冻结在关机前一刻却继续当现状显示）。这里把 index.html 里的脚本抽出来，
 * 在 Node 里喂假数据、断言产出的 HTML。
 *
 * 跑法：node CENTRAL/GUI/screen/render-test.js
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const INDEX = path.join(__dirname, "web", "index.html");
const html = fs.readFileSync(INDEX, "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) {
  console.error("找不到 <script> 块");
  process.exit(1);
}
const code = m[1];

// 最小 DOM/网络替身：脚本加载时会自己 render() 一次并挂定时器
// ★ 元素按 id **复用同一个对象**，测试才能回读 innerHTML（早期版本每次返回新对象，读不到）
const els = {};
const stubEl = (id) => (els[id] = els[id] || {
  id, innerHTML: "", textContent: "", className: "", style: {},
  // 对话层用到的 DOM 能力（appendChild / scrollTop / focus）
  childNodes: [], appendChild(c) { this.childNodes.push(c); },
  scrollTop: 0, scrollHeight: 0, focus() {},
});
// ★ TIME 的三个子框现在是**常驻 DOM**（动画要求元素不被销毁重建），
//   内容分散在三个元素里 —— 断言时把它们合起来读。
const timeHtml = () =>
  ((els["box-next"] || {}).innerHTML || "") +
  ((els["box-due"] || {}).innerHTML || "") +
  ((els["box-alert"] || {}).innerHTML || "");
// AGENT 面板同样改成**常驻 DOM** 的四块（用户 2026-10-15：和 TIME 一样的选中/折叠）
const agentHtml = () =>
  ["aplan", "aslots", "arules", "awarn"]
    .map(k => (els["box-" + k] || {}).innerHTML || "").join("");
// ★ 记录 fetch 调用（推送测试要断言"掉回兜底时**立刻**取一次，不等 60 秒"）。
//   数组必须挂在 sandbox 上：追加的导出代码是在 vm 里跑的，看不到这个文件的局部变量。
const fetchCalls = [];
const sandbox = {
  console,
  document: { getElementById: stubEl, querySelector: stubEl, addEventListener: () => {},
              createElement: () => ({ className: "", textContent: "" }) },
  fetch: (u) => { fetchCalls.push(String(u)); return new Promise(() => {}); },  // 永不 resolve：测试不依赖网络
  __fetchCalls: fetchCalls,
  EventSource: function () { this.addEventListener = () => {}; this.close = () => {}; },
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: () => 0,
  clearTimeout: () => {},                      // 页面用它取消"标题打字机"的收尾定时器
};
vm.createContext(sandbox);
vm.runInContext(
  code +
  "\n;globalThis.__t = { card, netBlock, internetBlocks, wanBlock, MACHINES, nodes," +
  " renderTime, setTime: (v) => { timeData = v; }, applyTabs, nextTab, TABS," +
  " setHover, clearHover, activeIndex, bindTabs," +
  " moveFocus, scrollFocus, bindTimeBoxes, TIME_BOXES, setFocus: (v) => { focusBox = v; }," +
  " setting, renderSettings, bindSettings, SETTING_FIELDS, DEFAULTS," +
  " SETTINGS, applySettings, renderAgent, loadAgent, TIME_TAB_INDEX, AGENT_TAB_INDEX," +
  " setAgent: (v) => { agentData = v; }," +
  " setTabIndex: (i) => { tabIndex = i; applyTabs(); }," +
  " rotateMsFor, focusBoxOf: () => focusBox, chatOpen, chatClose, CHAT_URL," +
  " tabIndexOf: () => tabIndex, setLastRotateAt: (v) => { lastRotateAt = v; }," +
  " updateReplanHint, renderService, svcPick, rotateTick, renderSvcNode, render," +
  " kbdLocked: () => kbdLockedNow()," +
  " markKey: () => { lastKeyAt = Date.now(); focusSource = \"key\"; }," +
  " resetKeys: () => { lastKeyAt = 0; focusSource = \"\"; }," +
  " setSource: (v) => { focusSource = v; }," +
  " hoverIndexOf: () => hoverIndex," +
  " forceIdle: () => { lastMouseAt = 0; }," +
  // ── 推送体系（agent-plan 节点）用的出口 ──────────────────────────────────
  " applySnapshot, syncPush, pushGuard, frameFresh, planNodeFrame, PLAN_NODE, PLAN_SCHEMA, POLL_MS," +
  " setSseLive: (v) => { sseLive = v; }," +
  " setAgentFallback: (v) => { agentFallback = v; }," +
  " setTimeFallback: (v) => { timeFallback = v; }," +
  " pushSigOf: () => pushSig," +
  " agentOf: () => agentData, timeOf: () => timeData," +
  " fetchCalls: () => __fetchCalls.slice()," +
  " lightSet, lightSummary, LIGHT_MODULES, LAMP_RANK, LIGHT_STALE_MS," +
  " renderMasters, bindMasters, LIGHT_SUPPRESSED, lightSuppressNewest," +
  " masterSigOf: () => masterLastPress };",
  sandbox
);
const t = sandbox.__t;

// ---------------------------------------------------------------- 假数据
function sample(stale) {
  return {
    node: "central",
    stale_seconds: stale,
    cpu: { pct: 12.5, cores: 8, temp_c: 30 },
    mem: { used_pct: 29, used_mb: 4600, total_mb: 16000 },
    net: {
      rx_kbps: 5.7, tx_kbps: 0.3, latency_ms: 0.5,
      ifaces: [{ name: "enp2s0", up: true, speed_mbps: 1000, rx_kbps: 5.7, tx_kbps: 0.3 }],
      wan: { ok: true, loss_pct: 0, latency_ms: 44.1, target: "223.5.5.5", n: 3 },
    },
  };
}

const CENTER = { id: "central", name: "CENTER", note: "127.0.0.1" };
const checks = [];
function check(name, cond, detail) {
  checks.push({ name, ok: !!cond, detail });
}

// 1) 新鲜：正常显示数值、竖线绿、状态 OL
t.nodes.set("central", sample(2));
let h = t.card(CENTER) + t.netBlock(CENTER) + t.wanBlock();
check("新鲜：状态 OL", h.includes("OL"), h.slice(0, 120));
check("新鲜：显示数值", h.includes("12.5") || h.includes("13") || h.includes("12"), "CPU 值");
check("新鲜：竖线绿(ok)", h.includes("card ok") || h.includes("netcard ok"), "class");

// 2) 断联 12 秒：黄 + LOST，但数据仍显示（阈值 ≥10s）
t.nodes.set("central", sample(12));
h = t.card(CENTER) + t.netBlock(CENTER) + t.wanBlock();
check("≥10s：状态 LOST", h.includes("LOST"), h.slice(0, 120));
check("≥10s：竖线黄(warn)", h.includes("card warn") || h.includes("netcard warn"), "class");
check("≥10s：数据仍在（还没到关机）", !h.includes("OFFLINE"), "不应出现 OFFLINE");

// 3) 断联 30 秒（当关机，阈值 ≥15s）：灰 + OFFLINE + **冻结数值必须消失**
t.nodes.set("central", sample(30));
const cardHtml = t.card(CENTER);
const netHtml = t.netBlock(CENTER) + t.wanBlock();
h = cardHtml + netHtml;
check("≥15s：出现 OFFLINE", h.includes("OFFLINE"), h.slice(0, 160));
check("≥15s：竖线灰(off)", h.includes("card off") || h.includes("netcard off"), "class");
// 断言"整行标签消失"而不是"数字消失"：状态里的 "30 秒前" 也含数字，用数字会误判
check("≥15s：CPU 行已消失", !cardHtml.includes(">CPU<"), "冻结值必须被盖掉");
check("≥15s：TEMP 行已消失", !cardHtml.includes(">TEMP<"), "冻结值必须被盖掉");
check("≥15s：RAM 行已消失", !cardHtml.includes(">RAM<"), "冻结值必须被盖掉");
check("≥15s：UP/DOWN 行已消失", !netHtml.includes(">UP<") && !netHtml.includes(">DOWN<"), "冻结值必须被盖掉");
check("≥15s：PING/LOSS 行已消失", !netHtml.includes(">PING<") && !netHtml.includes(">LOSS<"), "冻结值必须被盖掉");
// ★ 用户 2026-10-23："离线的机器依然要显示名称" —— 名字必须在，不能被状态挤掉
check("★ 离线时**机器名仍在**（CENTER / MAIN 这类名字不许消失）",
  cardHtml.includes("CENTER") && netHtml.includes("CENTER"), "离线也要有名字");
check("★ 名字在 CSS 里**不可压缩**（`.mname` flex:0 0 auto）+ 状态可截断（`.state` 带 ellipsis）",
  /\.mname\s*\{[^}]*flex:\s*0 0 auto/.test(html) &&
  /\.state\s*\{[^}]*text-overflow:\s*ellipsis/.test(html) &&
  /\.state\s*\{[^}]*min-width:\s*0/.test(html), "名字优先");

// ---------------------------------------------------------------- TIME 面板
// 假的内核输出（形状照抄 `qyn.py sessions` / `anomalies` 的真实返回）
const TIME_OK = {
  ok: true,
  sessions: {
    now: { date: "2026-10-14", time: "14:09", weekday: "三", phase: "下午" },
    next: { course: "示例课程F", time: "14:20-18:10", periods: "5-8",
            location: "教学楼 示例教室X1", starts_in: "10 分钟后" },
    // 内核按时间先后给课次（next 就是 sessions[0]）；date 用于"按天铺开"
    sessions: [
      { course: "示例课程F", time: "14:20-18:10", periods: "5-8",
        location: "教学楼 示例教室X1", starts_in: "10 分钟后", date: "2026-10-14" },
      { course: "示例课程G", time: "08:55-11:50", periods: "2-4",
        location: "教学楼 示例教室X1", starts_in: "18 小时后", date: "2026-10-15" },
      { course: "示例课程B", time: "14:20-16:00", periods: "5-6",
        location: "教学楼 示例教室X1", starts_in: "19 小时后", date: "2026-10-15" },
    ],
    due: [
      { task: "过期的（应隐藏）", why: "还剩 0 分钟" },
      { task: "示例课程F", why: "还剩 30 分钟", gist: "上课前写完，当堂交纸质" },
      { task: "示例课程C 作业3", why: "还剩 5 小时", gist: "Pxx 第 1/2/3 题" },
      { task: "示例课程作业", why: "还剩 3 天", due_time: "上课前（周五 节3-4，10:10 前）" },
      { task: "第五件", why: "还剩 10 小时", gist: "描述五" },
      { task: "第六件", why: "还剩 20 小时", gist: "描述六" },
      { task: "第七件", why: "还剩 40 小时", gist: "描述七" },
      { task: "第八件（应被截掉）", why: "还剩 50 小时", gist: "描述八" },
    ],
  },
  anomalies: { clean: false, anomalies: [
    { severity: "medium", task: "示例课程A作业", detail: "锚不到具体日期" },
    { severity: "medium", task: "示例课程F", detail: "周期任务锚不到日期" },
    { severity: "medium", task: "示例课程F", detail: "周期任务锚不到日期" },
    { severity: "low", task: "第四条异常", detail: "用于验证不截断" },
  ] },
};

t.setTime(TIME_OK);
t.renderTime();
let th = timeHtml();
check("TIME：一口气显示两节课",
  th.includes("示例课程F") && th.includes("示例课程G"),
  "NEXT 两节");
check("TIME：标明节数", th.includes("NEXT · 2 节"), th.slice(0, 100));
check("TIME：教室独立在右侧(tright>tloc)",
  th.includes('class="tright"') && /class="tright"[\s\S]{0,80}class="tloc"/.test(th),
  "教室位置");
check("TIME：教室内容正确", th.includes(">教学楼 示例教室X1<"), "location");
check("TIME：左侧 = **日期** + 时间 + 节次（用户 2026-10-16：必须写清是哪天）",
  /class="tmeta">(今天|\d{2}-\d{2}) · 14:20-18:10 · 节 5-8</.test(th), "tmeta");
check("TIME：NEXT 不再显示「还剩多久」（用户 2026-10-15 删掉以省纵向空间）",
  !th.includes("10 分钟后") && !th.includes("18 小时后") && th.includes("教学楼 示例教室X1"), "省空间");
check("TIME：显示待办", th.includes("示例课程F") && th.includes("示例课程C 作业3"), "DUE");
check("TIME：DDL 0–2 小时标红", th.includes("trow red") && th.includes("还剩 30 分钟"), "red");
check("TIME：DDL 2–24 小时标黄", th.includes("trow yellow") && th.includes("还剩 5 小时"), "yellow");
check("TIME：DDL 24 小时以上标绿", th.includes("trow green") && th.includes("还剩 3 天"), "green");
check("TIME：过期的 DDL 不显示", !th.includes("过期的（应隐藏）") && !th.includes("还剩 0 分钟"),
  "过期项");
check("TIME：显示异常", th.includes("异常 4 条") && th.includes("示例课程A作业"), "ALERT");
check("TIME：异常**不截断**（全部渲染，靠滚动看）",
  th.includes("示例课程F") && th.includes("第四条异常"), "4 条都在");
check("TIME：DUE 折叠态默认 5 条（用户 2026-10-15 又改回 5）",
  (th.match(/class="trow (red-blink|red|yellow|green)"/g) || []).length === 5, "5 条");
check("TIME：每条待办都是「主行 + 描述行」（不是只平铺名字）",
  (th.match(/class="tgist"/g) || []).length >= 5, "描述行");
check("TIME：描述缺失时退到 due_time（保证一个个都有描述）",
  /const desc = d\.gist \|\| d\.due_time \|\| ""/.test(code), "兜底");
// DUE 分级（用户 2026-10-15）：≤6h 红+闪 / 6–12h 红 / 12–24h 黄 / 更远绿
check("DUE 分级：≤6 小时 = 红 + 闪（30 分钟、5 小时两条）",
  (th.match(/class="trow red-blink"/g) || []).length === 2 &&
  // 2026-11-06：闪烁不再各自挂 animation，改由**全局时钟** body.flash-on 驱动
  // （用户："闪光效果没有实现同步"）
  /body\.flash-on \.trow\.red-blink/.test(html), "红闪（全局时钟）");
check("DUE 分级：6–12 小时 = 红**不闪**（10 小时那条）",
  (th.match(/class="trow red"/g) || []).length === 1 &&
  !/\.trow\.red\s+\.twhy\s*\{[^}]*animation/.test(html), "红不闪");
check("DUE 分级：12–24 小时 = 黄（20 小时那条）",
  (th.match(/class="trow yellow"/g) || []).length === 1, "黄");
check("DUE 分级：更远 = 绿（折叠态可见的 3 天那条）",
  (th.match(/class="trow green"/g) || []).length >= 1, "绿");
// ★ 用户 2026-10-23："12 月的 示例平台作业DDL 为什么没有渲染进 DUE" ——
//   根因在**推送端窗口**（`sessions --days 7` 把 12-28 挡掉了 ✗），屏幕端本来就不过滤日期 ✓。
//   这条测试把"屏幕端不许加日期过滤"钉住：内核给什么就渲染什么 ✓
check("★ DUE **不按日期过滤**：远期 DDL（12-28）在 dueMax 条数内就必须渲染出来",
  (() => {
    const F = JSON.parse(JSON.stringify(TIME_OK));
    F.sessions.due = [
      { task: "示例平台作业", why: "还剩 66 天", date: "2026-12-28",
        due_time: "09:00", gist: "线上平台全部 task 做完并提交" },
      { task: "示例平台作文", why: "还剩 44 天", date: "2026-12-06", gist: "12/6 23:59 前提交" },
    ];
    t.setTime(F); t.renderTime();
    const h = timeHtml();
    t.setTime(TIME_OK); t.renderTime();
    return h.includes("示例平台作业") && h.includes("示例平台作文") &&
           /class="trow green"/.test(h);
  })(), "远期 DDL 上屏");
// ★ 用户 2026-10-23："顺序乱了，DDL 应该严格按照时间顺序排，和显示多少条无关"
check("★ DUE **严格按日期升序**（屏幕端自己再排一次，不信输入顺序）；无日期的排最后",
  (() => {
    const F = JSON.parse(JSON.stringify(TIME_OK));
    F.sessions.due = [
      { task: "远期·十二月", why: "还剩 60 天", date: "2026-12-28", gist: "x" },
      { task: "近期·十月", why: "还剩 2 天", date: "2026-10-25", gist: "y" },
      { task: "无日期·复习", why: "没有 DDL 日期", gist: "z" },
    ];
    t.setTime(F); t.renderTime();
    const h = timeHtml();
    t.setTime(TIME_OK); t.renderTime();
    const iOct = h.indexOf("近期·十月");
    const iDec = h.indexOf("远期·十二月");
    const iNone = h.indexOf("无日期·复习");
    return iOct >= 0 && iDec > iOct && iNone > iDec;
  })(), "日期排序");
check("TIME：截断时告知总数", th.includes("共 7") && !th.includes("第八件"), "截断提示");
check("TIME：三块带 kind 类（next / due / alert）",
  els["box-next"].className.includes("next") && els["box-due"].className.includes("due") &&
  els["box-alert"].className.includes("alert"),
  `next=${els["box-next"].className} due=${els["box-due"].className} alert=${els["box-alert"].className}`);
check("TIME：三个框是**常驻 DOM**（动画的前提：元素不能被重建）",
  /id="box-next"/.test(html) && /id="box-due"/.test(html) && /id="box-alert"/.test(html) &&
  !/function timeCard\(/.test(code), "常驻 DOM");

// 收起态的短名（用户 2026-10-14）：全名 + 短名都渲染，靠 CSS 切换
t.nodes.set("central", sample(2));
// MAIN 也要有数据，否则它那块（含 MAIN LAN / MAIN WLAN）走的是"未上报"分支
const MAIN_SAMPLE = sample(2);
MAIN_SAMPLE.node = "win-dev";
MAIN_SAMPLE.net.ifaces = [
  { name: "以太网", up: true, speed_mbps: 1000, rx_kbps: 2, tx_kbps: 1 },
  { name: "WLAN",   up: true, speed_mbps: 287,  rx_kbps: 3, tx_kbps: 4 },
];
t.nodes.set("win-dev", MAIN_SAMPLE);
const dcHtml = t.MACHINES.map(t.card).join("");
check("短名：WORKSTATION 同时渲染全名与 WS",
  dcHtml.includes('<span class="mfull">WORKSTATION</span>') &&
  dcHtml.includes('<span class="mabbr">WS</span>'), "WS");
const netHtmlAll = t.MACHINES.map(t.netBlock).join("") + t.internetBlocks();
check("短名：MAIN LAN / MAIN WLAN 有短名 M LAN / M WLAN",
  netHtmlAll.includes('<span class="mfull">MAIN LAN</span>') &&
  netHtmlAll.includes('<span class="mabbr">M LAN</span>') &&
  netHtmlAll.includes('<span class="mfull">MAIN WLAN</span>') &&
  netHtmlAll.includes('<span class="mabbr">M WLAN</span>'), "M LAN / M WLAN");
check("短名：CENTER / SERVER 这种本来就短的不做短名",
  !/mfull">CENTER<\/span><span class="mabbr/.test(dcHtml), "CENTER 无短名");
check("TIME：标题栏用内核的日期/周几",
  els["time-note"].textContent.includes("2026-10-14") && els["time-note"].textContent.includes("三"),
  els["time-note"].textContent);

// 桥挂了：必须显示"读不到"，不能空白
t.setTime({ ok: false, error: "桥不可用：TypeError" });
t.renderTime();
th = timeHtml();
check("TIME：桥不可用时显示读不到", th.includes("读不到"), th.slice(0, 120));
check("TIME：桥不可用时状态栏提示", els["time-note"].textContent.includes("内核不可用"),
  els["time-note"].textContent);

// ---------------------------------------------------------------- CSS 一致性
// 用户 2026-10-14：教室与课程名统一成**更大的那个**字号。这条只有 CSS 能表达，
// 所以直接检查样式表（渲染测试平时不看样式，这条是刻意的例外）。
const cssRaw = (html.match(/<style>([\s\S]*?)<\/style>/) || [])[1] || "";
// 解析前先**剥掉注释** —— 否则注释会和后面的选择器黏在同一个"选择器文本"里，
// 导致 `sels.includes(".tcourse")` 永远为 false（这条断言第二次跑就踩到了）。
const css = cssRaw.replace(/\/\*[\s\S]*?\*\//g, "");
// 只认**选择器就是这个类**的规则 —— 不能拿 ".panel.collapsed .tloc" 那种派生规则来比，
// 否则收起态的字号会把展开态的字号顶掉（这条断言第一次跑就踩到了）。
function fontSizeOf(selector) {
  for (const block of css.split("}")) {
    const parts = block.split("{");
    if (parts.length < 2) continue;
    const sels = parts[0].split(",").map(s => s.trim());
    if (!sels.includes(selector)) continue;
    const m = parts[1].match(/font-size:\s*([0-9.]+)px/);
    if (m) return parseFloat(m[1]);
  }
  return null;
}
const szCourse = fontSizeOf(".tcourse");
const szLoc = fontSizeOf(".tloc");
check("CSS：教室与课名同字号", szCourse !== null && szCourse === szLoc,
  `.tcourse=${szCourse}px .tloc=${szLoc}px`);

// ---------------------------------------------------------------- 标签页：收起 / 依次展开
// 五块 = **三小两大**（SERVICE / DC / NET 小=权重1；TIME / AGENT 大=权重2）；
// 同一时刻只有一块展开，整行按权重刚好铺满（用户 2026-10-15 的方案）。
t.applyTabs();
check("标签页：第 1 块（SERVICE）展开、其余收起",
  !els["panel-service"].className.includes("collapsed") &&
  els["panel-net"].className.includes("collapsed") &&
  els["panel-time"].className.includes("collapsed"),
  `svc=${els["panel-service"].className} time=${els["panel-time"].className}`);
check("标签页：宽度是**内联 flex 权重**（basis 0 + grow；收起=权重、展开=双份）",
  els["panel-service"].style.flexBasis === "0" && els["panel-service"].style.flexGrow === "2" &&
  els["panel-net"].style.flexBasis === "0" && els["panel-net"].style.flexGrow === "1" &&
  els["panel-time"].style.flexGrow === "2" && els["panel-time"].style.flexShrink === "0",
  `svc=${els["panel-service"].style.flexGrow} net=${els["panel-net"].style.flexGrow} time=${els["panel-time"].style.flexGrow}`);
check("标签页：三小两大（权重 1 × 3、权重 2 × 2）",
  t.TABS.filter(x => x.weight === 1).length === 3 && t.TABS.filter(x => x.weight === 2).length === 2,
  t.TABS.map(x => x.weight).join(","));
check("布局：SERVICE 在 DATACENTER 左边（HTML 顺序）",
  /id="panel-service"[\s\S]*id="panel-dc"/.test(html), "顺序");
t.nextTab();
check("标签页：nextTab 轮换到第 2 块（DATACENTER）",
  !els["panel-dc"].className.includes("collapsed") && els["panel-service"].className.includes("collapsed"),
  `dc=${els["panel-dc"].className} svc=${els["panel-service"].className}`);
check("标签页：TIME 收起时保留 wide 基础类",
  els["panel-time"].className.startsWith("panel wide"), els["panel-time"].className);
check("标签页：小模块展开拿双份（DC grow 2）、大模块收起是 2",
  els["panel-dc"].style.flexGrow === "2" && els["panel-time"].style.flexGrow === "2",
  `dc=${els["panel-dc"].style.flexGrow} time=${els["panel-time"].style.flexGrow}`);

// 交互（用户 2026-10-15）：鼠标悬停放大、移走恢复轮转、方向键选择
t.setHover(3);          // 此刻自动轮转停在 DC（tabIndex=1），把鼠标移到 TIME 上
check("交互：悬停的面板被放大（盖过自动轮转）",
  !els["panel-time"].className.includes("collapsed") && els["panel-dc"].className.includes("collapsed"),
  `time=${els["panel-time"].className} dc=${els["panel-dc"].className}`);
check("交互：悬停时 TIME 权重翻倍（grow 4）",
  els["panel-time"].style.flexGrow === "4", els["panel-time"].style.flexGrow);
t.clearHover();
check("交互：鼠标移走后回到自动轮转选中的那个",
  !els["panel-dc"].className.includes("collapsed") && els["panel-time"].className.includes("collapsed"),
  `dc=${els["panel-dc"].className} time=${els["panel-time"].className}`);
t.nextTab(-1);
check("交互：方向键反着走（-1 回上一个：SERVICE）",
  !els["panel-service"].className.includes("collapsed"), els["panel-service"].className);
t.nextTab(-1);
check("交互：方向键到头会绕回最后一个（AGENT）",
  !els["panel-agent"].className.includes("collapsed"), els["panel-agent"].className);
t.bindTabs();
check("交互：bindTabs 在测试替身（无 DOM 事件）下也不报错", true, "");

// TIME 三个子框的"小光标"（用户 2026-10-15）：默认不变，选中才铺开全部 + 滚动
t.setTime(TIME_OK);
t.setFocus("next");
t.renderTime();
let fh = timeHtml();
check("子框：选中 NEXT 后铺开全部课次", fh.includes("示例课程B"), "全部课次");
check("子框：按天分组（出现 tday 小标题）", /class="tday"/.test(fh), "按天");
check("子框：选中后不再写（共 N）", !fh.includes("（共 3）"), "无截断提示");
check("子框：选中的框带 focus 高亮（在常驻元素上换 class，才有过渡）",
  els["box-next"].className.includes("focus") && !els["box-due"].className.includes("focus"),
  `next=${els["box-next"].className}`);
t.setFocus("due");
t.renderTime();
fh = timeHtml();
check("子框：选中 DUE 后给全部 7 条",
  (fh.match(/class="trow (red-blink|red|yellow|green)"/g) || []).length === 7, "全部 DUE");
t.setTabIndex(3);            // 面板**展开**（TIME）→ 不选中也要给全部
t.setFocus(null);
t.renderTime();
fh = timeHtml();
// ★ 用户 2026-10-17 改口径："不要切过去就全部展开，应该是选中哪个哪个展开"
check("★ 只展开**选中的那块**：不选中时 DUE 就是收起量（5 条 + 共 7）",
  (fh.match(/class="trow (red-blink|red|yellow|green)"/g) || []).length === 5 &&
  fh.includes("共 7"), "不选中不展开");
t.setTabIndex(0);            // 面板收起 → 回到 dueMax 截断
t.renderTime();
fh = timeHtml();
check("折叠态：DUE 只给 5 条 + 标注共 7",
  (fh.match(/class="trow (red-blink|red|yellow|green)"/g) || []).length === 5 && fh.includes("共 7"),
  "折叠截断");
t.setTabIndex(2);
t.renderTime();
check("CSS：子项不许被压扁（否则描述行会被挤成 0 高）",
  /\.tcard\s*>\s*\*\s*\{[^}]*flex:\s*0 0 auto/.test(css), "不压扁");

// 用户 2026-10-15：**鼠标**切走时内容必须重算（只换 class 不重画 ⇒ 停在展开态的样子 ✗）
t.setTime(TIME_OK);
t.setTabIndex(3);
t.setFocus("next");            // ★ 选中 NEXT ⇒ 展开（用户 2026-10-17：内容只看选中，不看面板）
t.renderTime();
const nextExpanded = els["box-next"].innerHTML;
t.setFocus(null);                 // ★ 取消**选中** ⇒ 内容回到收起量
t.clearHover();                   // clearHover 走 refreshPanels（唯一入口）⇒ 触发重画
const nextCollapsed = els["box-next"].innerHTML;
check("取消选中后内容重算（NEXT 从「全部 3 节」回到「2 节（共 3）」）",
  nextExpanded !== nextCollapsed && nextCollapsed.includes("共 3") &&
  nextExpanded.includes("NEXT · 3 节"), "重算");
check("★ 内容只看**选中**：时间面板不再有\"展开就给全部\"的开关（timeExpanded 已删）",
  !/timeExpanded/.test(code) && /const classes = focusNext \?/.test(code) &&
  /const due = focusDue \?/.test(code), "只看选中");
check("CSS：选中的子框带**内描边蓝框**（box-shadow 不会被等级色规则盖掉）",
  /\.tcard\.focus\s*\{[^}]*box-shadow:\s*inset 0 0 0 1px var\(--accent\)/.test(html), "蓝框");
check("刷新：换面板时**重画机器卡**（否则旧面板那张卡的焦点蓝框会留下）",
  /function renderCards\(\)/.test(code) && /renderCards\(\);/.test(code) &&
  /renderCards\(\);\s*\/\/ 监视栏/.test(code), "重画卡片");
check("切换面板只有一条路径 refreshPanels（换 class + 重画）",
  /function setHover\(i\) \{[^}]*refreshPanels\(\)/.test(code) &&
  /function refreshPanels\(\)/.test(code), "唯一入口");
t.clearHover();
t.setTabIndex(3);              // TIME（SERVICE 加入后索引整体后移 1，这里原来是 2 ✗）
t.renderTime();
// 光标现在作用在**当前展开的面板**上（用户 2026-10-15）：TIME 用 TIME 的三块，AGENT 用 AGENT 的四块
t.setFocus(null);
t.setTabIndex(3);                 // TIME（索引 3；2 是 NETWORK）
t.moveFocus(1);
check("子框：TIME 展开时 ↑/↓ 选中第一个子框",
  !els["panel-time"].className.includes("collapsed") && els["box-next"].className.includes("focus"),
  `time=${els["panel-time"].className} next=${els["box-next"].className}`);
t.setTabIndex(4);                 // AGENT
t.setFocus(null);
t.moveFocus(1);
check("子框：AGENT 展开时 ↑/↓ 作用在 AGENT 的四块上",
  !els["panel-agent"].className.includes("collapsed") && els["box-aplan"].className.includes("focus"),
  `agent=${els["panel-agent"].className} aplan=${els["box-aplan"].className}`);
t.setFocus(null);
// 用户 2026-10-15：换内容会把 scrollTop 归零 ⇒ 一按方向键内容就跳回顶端
els["box-due"].scrollTop = 40;
t.setFocus(null);
t.renderTime();
t.SETTINGS.dueMax = 3;        // 改设置 ⇒ DUE 内容确实变了 ⇒ 必须走"换 innerHTML"这条路
t.renderTime();
check("子框：换内容时保住滚动位置（否则会跳回顶端）",
  els["box-due"].scrollTop === 40, `scrollTop=${els["box-due"].scrollTop}`);
t.SETTINGS.dueMax = t.DEFAULTS.dueMax;
t.renderTime();

// ---------------------------------------------------------------- 设置界面（用户 2026-10-15）
check("设置：默认值可读",
  t.setting("fastSec") === 5 && t.setting("slowSec") === 15 && t.setting("dueMax") === 5,
  `fast=${t.setting("fastSec")} slow=${t.setting("slowSec")} dueMax=${t.setting("dueMax")}`);
t.renderSettings();
check("设置：面板按字段表生成输入框",
  (els["set-body"].innerHTML.match(/data-key=/g) || []).length === t.SETTING_FIELDS.length,
  `字段数 ${t.SETTING_FIELDS.length}`);
// 改设置必须**立刻影响渲染**（否则设置界面就是摆设）
t.setTabIndex(0);             // 让 TIME 收起（展开态本来就给全部，测不出 dueMax）
t.SETTINGS.dueMax = 3;
t.setFocus(null);
t.renderTime();
check("设置：改了 DDL 条数立刻生效（3 条）",
  (timeHtml().match(/class="trow (red-blink|red|yellow|green)"/g) || []).length === 3,
  "3 条");
t.SETTINGS.dueMax = t.DEFAULTS.dueMax;
t.renderTime();
check("设置：齿轮与面板都在 HTML 里",
  /id="gear"/.test(html) && /id="settings"/.test(html), "gear/settings");
// 用户 2026-10-16：设置 = **整屏独立界面 + 分类展示**；齿轮移到**右下角**；时钟带日期
check("设置：**整屏**独立界面（fixed + inset:0）+ 分类卡（.set-group / grid）",
  /#settings\s*\{[^}]*position:\s*fixed/.test(html) &&
  /#settings\s*\{[^}]*inset:\s*0/.test(html) &&
  /\.set-body\s*\{[^}]*grid/.test(html) &&
  /\.set-group\s*\{/.test(html), "全屏分类");
check("设置：分类表存在 + 未归类字段有「其他」兜底（不会漏字段）",
  /SETTING_GROUPS/.test(code) && /其他/.test(code) && /flatMap/.test(code), "分类兜底");
check("设置：齿轮移到**右下角**（fixed + right + bottom）",
  /#gear\s*\{[^}]*position:\s*fixed/.test(html) &&
  /#gear\s*\{[^}]*right:/.test(html) && /#gear\s*\{[^}]*bottom:/.test(html), "右下角");
check("时钟：**日期 + 时间**（getFullYear + 补零）",
  /getFullYear\(\)/.test(code) && /padStart\(2,\s*"0"\)/.test(code), "日期");
check("时钟：日期**单独放大**（`.clk-d` 36px > 时刻 30px；不许整串同字号 ✗，用户 2026-10-19）",
  /<span id="clock"><span class="clk-d">/.test(html) &&
  /#clock \.clk-d\s*\{[^}]*font-size:\s*36px/.test(html) &&
  /#clock \.clk-t\s*\{[^}]*font-size:\s*30px/.test(html) &&
  /el\.querySelector\("\.clk-d"\)/.test(code), "日期更大");
check("设置：阈值全部走 setting()，不留写死的数字",
  !/levelOf\(cpu\.pct,\s*75/.test(code) && !/slice\(0, 2\)/.test(code), "无写死");
check("CSS：纵向尺寸动画已按要求去掉，只留颜色过渡（**面板宽度**的 grow 过渡是另一回事）",
  /\.tcard\s*\{[^}]*transition:\s*border-color/.test(css) &&
  !/\.tcard[^{]*\{[^}]*transition:[^;}]*\b(max-height|flex-grow)\b/.test(css), "无尺寸动画");
// 用户 2026-10-15 报的闪烁根因：生长必须靠**显式数值上限**，不能靠 flex-basis:auto
check("CSS：三个框的收起上限是显式数值",
  /\.tcard\.next:not\(\.grow\)\s*\{[^}]*max-height:\s*152px/.test(css) &&
  /\.tcard\.due:not\(\.grow\)\s*\{[^}]*max-height:\s*360px/.test(css) &&
  /\.tcard\.grow\s*\{[^}]*max-height:\s*2000px/.test(css), "显式上限");
check("CSS：滚动条不再推动布局（scrollbar-gutter: stable）",
  /\.tcard\s*\{[^}]*scrollbar-gutter:\s*stable/.test(css), "gutter");
// 键盘（用户 2026-10-15：鼠标停着时键盘"失效"、方向键变成滚内部内容）
// ★ 行为级：键盘"展开"（↑/↓ 在子框间移动小光标）。用户 2026-10-17 报"整个崩了"。
t.setTabIndex(0);                 // 停在 SERVICE
t.setFocus(null);
t.moveFocus(1);
check("键盘：SERVICE 里 ↓ 选中**第一张卡**（svc-body）",
  t.focusBoxOf() === "svc-body", `focus=${t.focusBoxOf()}`);
check("键盘：SERVICE 四张卡依次可选中（svc-body → svc-ds → svc-wechat → svc-agent → 循环）",
  (() => {
    t.setTabIndex(0);
    t.setFocus(null);
    const seen = [];
    for (let i = 0; i < 5; i++) { t.moveFocus(1); seen.push(t.focusBoxOf()); }
    return seen.join(",") === "svc-body,svc-ds,svc-wechat,svc-agent,svc-body";
  })(), "四张卡");
check("键盘：DATACENTER / NETWORK 的**机器卡**也能被选中",
  (() => {
    t.setTabIndex(1);
    t.setFocus(null);
    t.moveFocus(1);
    const dc = t.focusBoxOf();
    t.setTabIndex(2);
    t.setFocus(null);
    t.moveFocus(1);
    return dc === "dc-central" && t.focusBoxOf() === "net-central";
  })(), "机器卡");
check("键盘：面板映射按 **id** 而不是数字索引（防止再出现过期索引）",
  /const id = \(TABS\[tabIndex\] \|\| \{\}\)\.id/.test(code) &&
  /if \(id === "panel-dc"\) return DC_BOXES/.test(code), "按 id 映射");
check("键盘：选中卡带 .focus（机器卡模板里带、SERVICE 卡由 applyCardFocus 刷）",
  /focusBox === "dc-" \+ m\.id \? " focus"/.test(code) &&
  /function applyCardFocus\(\)/.test(code) && /el\.className = want/.test(code), "focus 类");
check("CSS：选中卡**只高亮、不放大**（用户 2026-10-17：「datacenter 有选中放大的效果 不需要」✗）",
  /\.card\.focus\s*\{[^}]*border-color:\s*var\(--accent\)/.test(html) &&
  !/\.card\.focus\s*\{[^}]*flex:\s*2/.test(html) &&
  /\.svc\.focus \.card\s*\{/.test(html), "只高亮");
check("★ 对话区不再覆盖全屏：**左半宽、贴在底条上方**拉起（用户 2026-10-20）",
  /#chat\s*\{[^}]*position:\s*fixed/.test(html) &&
  /#chat\s*\{[^}]*width:\s*50%/.test(html) &&
  /#chat\s*\{[^}]*bottom:\s*57px/.test(html) &&               // body 内距 14 + 底条 43 = 57 ✓
  !/#chat\s*\{[^}]*inset:\s*0/.test(html) &&                 // 旧的"覆盖全屏"已不在 ✗
  // ★ 2026-11-09：底条 50% → **40%**，右边腾出空档放「⟳ 重排」按钮 ✓
  //   （50% 时按钮会和 MASTER WARNING 撞上 ✗ 实测间距只有 ~10px ✗）
  /#dock\s*\{[^}]*width:\s*40%/.test(html) &&
  /#dock-replan\s*\{[^}]*left:\s*calc\(40%/.test(html) &&      // 按钮在对话条右边的空档 ✓
  /#dock\s*\{[^}]*align-self:\s*flex-start/.test(html) &&
  /#gear\s*\{[^}]*right:\s*22px;\s*bottom:\s*14px/.test(html) &&   // 齿轮落在右下角、与底条同一行 ✓
  /if \(t && typeof t\.focus === "function"\) t\.focus\(\)/.test(code), "左半拉起 + 右下齿轮");
check("★ 头栏不许被「迟到的轮询响应」写坏（SSE 已恢复却永远显示轮询 ✗）",
  (() => {
    const m = code.match(/function startPolling\(\)[\s\S]*?\n}/);
    if (!m) return false;
    // ★ 先剥掉注释再判 —— 注释里也会出现 `setConn("down")` 字样，直接匹配会误伤（我自己踩过 ✗）
    const body = m[0].replace(/\/\/[^\n]*/g, "");
    return /setConn\("down"\);/.test(body) &&                              // 进轮询时写一次 ✓
           !/applySnapshot\(await r\.json\(\)\);\s*setConn\("down"\)/.test(body) &&  // 旧的"无条件写 down"已不在 ✗
           /if \(!pollTimer\) return;/.test(body);                         // 「轮询已停就别写」的守卫 ✓
  })(), "迟到的轮询");
t.setTabIndex(3);                 // TIME
t.setFocus("next");
t.moveFocus(1);
check("键盘：TIME 里 ↓ 走到 due", t.focusBoxOf() === "due", `focus=${t.focusBoxOf()}`);
t.moveFocus(1);
check("键盘：TIME 里再 ↓ 走到 alert", t.focusBoxOf() === "alert", `focus=${t.focusBoxOf()}`);
t.moveFocus(1);
check("键盘：TIME 里 ↓ 到末尾**循环**回 next", t.focusBoxOf() === "next", `focus=${t.focusBoxOf()}`);
t.moveFocus(-1);
check("键盘：↑ 反着走回 alert", t.focusBoxOf() === "alert", `focus=${t.focusBoxOf()}`);
t.setTabIndex(4);                 // AGENT
t.setFocus("aplan");
t.moveFocus(1);
check("键盘：AGENT 里 ↓ 走到 aslots", t.focusBoxOf() === "aslots", `focus=${t.focusBoxOf()}`);
t.setFocus(null);
t.setTabIndex(0);
t.renderTime();
t.renderAgent();
check("初始化：**逐步隔离**（任何一步抛错都不许让键盘监听没注册）",
  /const INIT_ERRORS = \[\]/.test(code) && /function safeInit\(name, fn\)/.test(code) &&
  /safeInit\("bindTabs", bindTabs\)/.test(code) &&
  !/^applyTabs\(\);$/m.test(code) && !/^bindTabs\(\);$/m.test(code), "逐步隔离");
check("初始化：bindTabs 排在 applyTabs **之前**（键盘优先保住）",
  code.indexOf('safeInit("bindTabs"') < code.indexOf('safeInit("applyTabs"'), "键盘优先");
check("初始化：失败在顶栏可见（⚠ 初始化 N 步失败 + 悬停看原因）",
  /⚠ 初始化/.test(code) && /conn\.title = INIT_ERRORS\.join/.test(code), "失败可见");
check("键盘：单个动作包了 try/catch（一次渲染抛错不许让所有键失效）",
  /键盘动作失败/.test(code), "键盘容错");
// ★★ 键盘/鼠标**互斥锁**（用户 2026-10-17："键盘鼠标操作冲突，建议加个阻塞"）
check("互斥锁：键盘操作后 kbdHoldSec 秒内处于锁定（默认 30s，0 = 关闭）",
  (() => {
    t.resetKeys();
    const before = t.kbdLocked();
    t.markKey();
    return before === false && t.kbdLocked() === true && t.SETTINGS.kbdHoldSec === 30;
  })(), "锁定生效");
check("互斥锁：**键盘**选中的子框，不会被'鼠标闲置恢复'清掉",
  (() => {
    t.setTabIndex(3);
    t.setFocus("due");
    t.markKey();
    t.forceIdle();                 // 假装鼠标"闲置很久"
    t.rotateTick();
    return t.focusBoxOf() === "due";
  })(), "键盘选中保留");
check("互斥锁：**鼠标**给的子框，闲置恢复仍然清（老行为不回退）",
  (() => {
    t.setTabIndex(3);
    t.setFocus("due");
    t.resetKeys();                 // 不在键盘锁定期
    t.setSource("mouse");
    t.forceIdle();
    t.rotateTick();
    return t.focusBoxOf() === null;
  })(), "鼠标选中照清");
check("互斥锁：锁定期内**不自动轮转**（画面不被抢走）",
  /if \(kbdLocked\) return;/.test(code), "锁定期不轮转");
check("★ 无操作后**恢复轮转**（用户 2026-10-21 要求检查）：清悬停 → 重新计时 → 下一拍自动切块",
  (() => {
    t.resetKeys();                 // 不在键盘锁定期
    t.setTabIndex(0);
    t.setHover(1);                 // 鼠标停在第 2 块（老逻辑会一直压住轮转 ✗）
    const hovered = t.hoverIndexOf() === 1;
    t.forceIdle();                 // 鼠标闲置很久（超过 idleResumeSec）
    t.rotateTick();                // 第 1 拍：清悬停 + **重新计时**（设计如此：先让人看完这一块 ✓）
    const cleared = t.hoverIndexOf() === null && t.focusBoxOf() === null;
    const noJump = t.tabIndexOf() === 0;          // 这一拍不该立刻跳 ✗
    t.setLastRotateAt(Date.now() - 60000);        // 模拟"停留时间已过"
    t.rotateTick();                                // 第 2 拍：应当自动轮转 ✓
    return hovered && cleared && noJump && t.tabIndexOf() === 1;
  })(), "闲置恢复轮转");
check("★ 静止的指针**不许压住轮切**（kiosk 上指针常停在某块 ⇒ 以前要干等 20 秒 ✗，2026-10-26）",
  (() => {
    t.resetKeys();
    t.setTabIndex(0);
    t.setHover(2);                                // 指针静止停在面板 2
    t.forceIdle();                                // 但鼠标已经很久没动了
    t.rotateTick();                               // 第 1 拍：清悬停（同一拍不跳）
    t.setLastRotateAt(Date.now() - 60000);
    t.rotateTick();                               // 第 2 拍：应当照常轮切 ✓
    const advanced = t.tabIndexOf() !== 0;
    t.setHover(null);
    return advanced;
  })(), "静止指针不挡轮切");
check("互斥锁：鼠标的两个入口都被挡（面板 mouseenter + 子框 mouseover / mouseleave）",
  (code.match(/kbdLockedNow\(\)\) return;/g) || []).length >= 2 &&
  /if \(!kbdLockedNow\(\)\) setHover\(i\)/.test(code), "鼠标被挡");
check("互斥锁：可在设置面板调（kbdHoldSec，默认 30s）",
  /key: "kbdHoldSec"/.test(code) && /kbdHoldSec: 30/.test(code) &&
  /"idleResumeSec", "kbdHoldSec"/.test(code), "可配置");
check("键盘：按键先取消悬停覆盖",
  /clearHover\(\);\s*nextTab\(1\)/.test(code) && /clearHover\(\);\s*moveFocus\(1\)/.test(code),
  "清悬停");
check("键盘：方向键 preventDefault（否则会去滚内部内容）",
  /e\.preventDefault\(\)/.test(code), "preventDefault");
// AGENT 面板的空间分配（用户 2026-10-15：时段被挤没了、依据/软偏好占太多）
check("CSS：AGENT 用**显式 flex** 保证时段永远拿剩余空间",
  /\.tcard\.agent-slots\s*\{[^}]*flex:\s*1 1 0/.test(css) &&
  /\.tcard\.agent-plan\s*\{[^}]*flex:\s*0 0 auto;\s*max-height:\s*140px/.test(css) &&
  /\.tcard\.agent-rules\s*\{[^}]*flex:\s*0 0 auto;\s*max-height:\s*40px/.test(css) &&
  /\.tcard\.agent-warn\s*\{[^}]*max-height:\s*40px/.test(css), "AGENT 分配");
check("CSS：AGENT 三行配色（gist 白 / 理由灰 / 紧迫度三档）",
  /\.agent-gist\s*\{[^}]*color:\s*var\(--fg\)/.test(css) &&
  /\.agent-why\s*\{[^}]*color:\s*var\(--dim\)/.test(css) &&
  /\.u-normal\s*\{[^}]*color:\s*var\(--fg\)/.test(css) &&
  /\.u-urgent\s*\{[^}]*color:\s*var\(--warn\)/.test(css) &&
  /\.u-red\s*\{[^}]*color:\s*var\(--bad\)/.test(css), "配色");
check("CSS：AGENT 依据/偏好**平时一直折叠**，选中才展开",
  /\.tcard\.agent-rules\s+\.tsub,/.test(css) &&
  /\.tcard\.agent-rules\.grow\s+\.tsub/.test(css) &&
  /\.tcard\.agent-rules\.grow,\s*\.tcard\.agent-warn\.grow\s*\{[^}]*flex:\s*1 1 auto/.test(css),
  "收起精简");
check("CSS：AGENT 规划块够放约 3 行、字号更大（140px / 21px）",
  /\.tcard\.agent-plan\s*\{[^}]*max-height:\s*140px/.test(css) &&
  /\.tplan\s*\{[^}]*font-size:\s*21px/.test(css), "规划 3 行");
check("CSS：折叠态只放大课程名（教室名不动），展开态回到 22px",
  /\.panel\.collapsed\s+\.tcourse\s*\{[^}]*font-size:\s*19px/.test(css) &&
  /\.panel\.collapsed\s+\.tloc\s*\{[^}]*font-size:\s*17px/.test(css) &&
  /\.tcourse\s*\{[^}]*font-size:\s*22px/.test(css), "只改课程名");
check("CSS：AGENT 时段字号收小（19 / 15 / 14）",
  /\.tcard\.agent-slots\s+\.tcourse\s*\{[^}]*font-size:\s*19px/.test(css) &&
  /\.agent-gist\s*\{[^}]*font-size:\s*15px/.test(css) &&
  /\.agent-why\s*\{[^}]*font-size:\s*14px/.test(css), "时段字号");
check("CSS：缩略态 NEXT 纵向翻倍（放得下两节课），展开态不变",
  /\.panel\.collapsed\s+\.tcard\.next:not\(\.grow\)\s*\{[^}]*max-height:\s*304px/.test(css) &&
  /\.tcard\.next:not\(\.grow\)\s*\{[^}]*max-height:\s*152px/.test(css), "NEXT 翻倍");
// ★ 用户 2026-10-18：中文标题改成**手写字图片**（白字/透明底），中英切换改成"书写 / 滑入"
check("CSS：标题中文 = 手写字图片（**内联 base64**，零外链）+ 两态叠在同一格",
  /#title\s*\{[^}]*display:\s*grid/.test(css) &&
  /#title > \*\s*\{[^}]*grid-area:\s*1 \/ 1/.test(css) &&
  /#title \.t-cn\s*\{[^}]*mask:\s*url\("data:image\/png;base64,/.test(css) &&
  /#title \.t-cn\s*\{[^}]*background:\s*var\(--metal\)/.test(css) &&
  /#title\.is-en \.t-cn\s*\{[^}]*opacity:\s*0/.test(css), "手写字标题");
check("CSS：标题**放大但不撑高顶栏、也不裁剪**（绝对定位真实 60px；顶栏仍 36px）",
  (() => {
    const box = /#title\s*\{[^}]*height:\s*36px;\s*position:\s*relative/.test(css);
    const abs = /#title \.t-cn\s*\{[^}]*position:\s*absolute;[^}]*height:\s*60px/.test(css);
    const noClip = !/#title\s*\{[^}]*overflow:\s*hidden/.test(css);   // 不许再裁 ✗
    const size = /#title \.t-cn\s*\{[^}]*\/\s*255px 60px/.test(css);   // 遮罩按 255×60 铺 ✓
    return box && abs && noClip && size;
  })(), "放大不撑高不裁剪");
check("CSS：切回中文有**书写**动效（mask 从左往右扫开），英文**滑入**",
  /@keyframes\s+qynWrite/.test(css) &&
  /@keyframes\s+qynWrite\s*\{\s*from\s*\{[^}]*clip-path:\s*inset\(0 100% 0 0\)/.test(css) &&
  /#title \.t-cn\.write\s*\{[^}]*animation:\s*qynWrite/.test(css) &&
  /#title \.t-en\s*\{[^}]*transform:\s*translateX\(16px\)/.test(css) &&
  /#title\.is-en \.t-en\s*\{[^}]*transform:\s*none/.test(css), "书写+滑入");
// 本轮新增：标题防烧屏轮换 / 打字机 / 分档轮换 / 换面板清光标
check("标题：中英轮换（防烧屏·只切类不换文字）",
  /id="title"/.test(html) && /QYN DATA CENTER/.test(html) && /TITLE_STATES/.test(code) &&
  /el\.className = cn \? "is-cn" : "is-en"/.test(code) &&
  !/el\.textContent = TITLES/.test(code), "中英轮换");
check("标题：重放书写动画时**不许清掉 t-cn 类**（否则手写图变空白 ✗）",
  /img\.className = "t-cn";/.test(code) &&
  /img\.className = "t-cn write";/.test(code) &&
  !/img\.className = "";/.test(code), "保住 t-cn");
check("CSS：展开切换时标题有颜色打字机",
  /\.panel\.typing\s+\.panel-title\s*\{[^}]*animation/.test(css) &&
  /@keyframes\s+qynTyping/.test(css), "打字机");
check("轮换：分档间隔（小的三块 5 秒 / 大的两块 15 秒）",
  t.rotateMsFor(0) === 5000 && t.rotateMsFor(1) === 5000 && t.rotateMsFor(2) === 5000 &&
  t.rotateMsFor(3) === 15000 && t.rotateMsFor(4) === 15000,
  `0=${t.rotateMsFor(0)} 2=${t.rotateMsFor(2)} 4=${t.rotateMsFor(4)}`);
// 严重 bug：换面板必须把子框光标清零，否则旧面板"记住"被放大的块
t.setFocus("next");
t.nextTab(1);
check("换面板时子框光标清零（否则缩小态还留着上次的放大）",
  t.focusBoxOf() === null, `focusBox=${t.focusBoxOf()}`);
check("CSS：.tcard[hidden] 显式声明（否则 display:flex 会盖掉 hidden）",
  /\.tcard\[hidden\]\s*\{[^}]*display:\s*none/.test(css), "hidden 覆盖");

// ---------------------------------------------------------------- 对话层（纯文字版）
check("对话：底部预留条是入口 + 对话层齐全",
  /id="dock"/.test(html) && /id="chat"/.test(html) && /id="chat-text"/.test(html) &&
  /id="chat-form"/.test(html), "对话 UI");
check("CSS：对话层从底部弹起并覆盖全屏",
  /#chat\s*\{[^}]*position:\s*fixed/.test(css) && /#chat\s*\{[^}]*translateY\(100%\)/.test(css) &&
  /#chat\.chat-open\s*\{[^}]*translateY\(0\)/.test(css), "滑上来");
check("对话：请求体用 text/plain（避开 CORS 预检）",
  /Content-Type":\s*"text\/plain/.test(code), "text/plain");
check("★ 对话：带 `client_msg_id`（幂等）+ 忙时 429 有明确提示、**不自动重试**（用户 2026-10-20）",
  /client_msg_id: mid/.test(code) &&
  /r\.status === 429/.test(code) &&
  /没有自动重试/.test(code) &&
  // 不许在失败后自动重发（不排队、不自动重试 ✓）
  !/setTimeout\([^;]{0,120}chatSend/.test(code), "幂等 + 429");
t.chatOpen();
check("对话：能打开（chat-open）", els["chat"].className === "chat-open", els["chat"].className);
t.chatClose();
check("对话：能关闭（chat-closed）", els["chat"].className === "chat-closed", els["chat"].className);
check("对话：桥端口是 /api/chat",
  t.CHAT_URL === "http://127.0.0.1:8091/api/chat", t.CHAT_URL);

// 触发策略（用户 2026-10-15）：计划表**最上面那条**完成后 → 按需重排
// 一键重排（用户 2026-11-08）：常驻按钮 + 桥的 /api/replan 接口
// （原来是"计划表最上面那条完成了才出现，点了走对话"—— 现在点一下直接发起一轮重排）
check("★ 一键重排：底栏按钮常驻，且调桥的 POST /api/replan（不再走对话）",
  /id="dock-replan"/.test(html) &&
  /REPLAN_URL = "http:\/\/127\.0\.0\.1:8091\/api\/replan"/.test(code) &&
  /async function replanNow/.test(code) &&
  /"POST"/.test(code) && /top_done/.test(code),
  "常驻 + /api/replan");
check("★ 一键重排：按钮点击绑的是 replanNow（不是 chatSend）",
  /dock-replan"[\s\S]{0,400}replanNow\(\)/.test(code) &&
  !/dock-replan"[\s\S]{0,400}chatSend\(/.test(code), "绑定 replanNow");
check("★ 一键重排：排完强制刷新（?force=1，否则桥的 15 秒缓存会显示旧规划）",
  /AGENT_URL \+ "\?force=1"/.test(code) && /TIME_URL \+ "\?force=1"/.test(code), "force 刷新");
t.setAgent({ ok: true, top_done: true, top_task: "示例课程C 作业3", plan: {}, rules: [] });
t.renderAgent();
t.updateReplanHint();
check("触发策略：top_done 时按钮显示且带上任务名",
  els["dock-replan"].hidden === false && /示例课程C 作业3/.test(els["dock-replan"].textContent),
  `hidden=${els["dock-replan"].hidden}`);
t.setAgent({ ok: true, top_done: false, plan: {}, rules: [] });
t.updateReplanHint();
check("★ 一键重排：没完成时按钮照常显示（只是不带 ⚠ / topdone 强调）",
  els["dock-replan"].hidden === false &&
  els["dock-replan"].className === "" &&
  !/⚠/.test(els["dock-replan"].textContent),
  `hidden=${els["dock-replan"].hidden} cls=${els["dock-replan"].className}`);

// ── SERVICE 面板：OpenCode 余额（用户 2026-10-15 的第一个子块）──────────────
// 契约：OPENCODEGO/apiusage/docs/INTERFACE.md —— 业务规则那边算好，这边只渲染。
const SVC_FRAME = {
  schema: "apiusage.panel/1", generated_at_ms: Date.now(), fresh_for_ms: 300000,
  thresholds: { go_low_percent: 15.0, balance_low_cny: 5.0 },
  level: "ok", headline: "Default 5 小时 剩余 $9.44（78.63%）",
  // ★ 展开态要用的真实字段（用户 2026-10-17）：消费 used_usd/limit_usd、采集口径 source、
  //   余额构成 granted/topped_up/low_threshold —— 按线上帧的形状来，测试才有意义。
  workspaces: [
    { label: "Default", source: "cookie（按工作区）", has_go: true, level: "ok", worst_window: "fiveHour", meters: [
      { window: "fiveHour", short: "5H", usable_percent: 78.63, remaining_usd: 9.44, used_usd: 0.44, limit_usd: 12.0, level: "ok", reset_in: "1 小时 30 分后重置" },
      { window: "week", short: "周", usable_percent: 38.0, remaining_usd: 11.4, used_usd: 18.6, limit_usd: 30.0, level: "ok", reset_in: "3 天后重置" },
      { window: "month", short: "月", usable_percent: 29.15, remaining_usd: 17.49, used_usd: 42.51, limit_usd: 60.0, level: "ok", reset_in: "20 天后重置" },
    ] },
    { label: "示例账号", source: "cookie（按工作区）·无 GO 订阅", has_go: false, level: "unknown", meters: [] },
  ],
  balances: [
    { account: "deepseek", currency: "CNY", total: 36.37, granted: 17.38, topped_up: 18.98,
      is_available: true, low_threshold: 5.0, level: "ok" },
  ],
};
t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: SVC_FRAME });
// 出口帧：OCGO 展开行里的"出口明细"要用它的 items（契约 qyn.svc-egress/1）
t.nodes.set("svc-egress", { node: "svc-egress", stale_seconds: 2, frame: {
  schema: "qyn.svc-egress/1", level: "ok", headline: "出口通",
  generated_at_ms: Date.now(), fresh_for_ms: 900000,
  items: [{ key: "出口", value: "yes", level: "ok" }, { key: "连续失败", value: "0" },
          { key: "端点", value: "opencode.ai/zen/go/v1/models" },
          { key: "检查时刻", value: "2026-10-17 02:23:19" }] } });
t.renderService();
let svc = els["svc-body"].innerHTML;
check("SERVICE：卡片头 **OCGO**（展开时接工作区名）+ 三条进度条；工作区名不再占一行",
  svc.includes('class="mname">OCGO') && (svc.match(/class="bar/g) || []).length >= 3 &&
  !/class="mname">Default</.test(svc) && !/row more"><span class="k">WS<\/span>/.test(svc), "OCGO");
// ★★ 卡的**展开态补充行**（用户 2026-10-17）：
//   "OCGO 展开应该展示目前工作区、通断状态、数据来源；DS API 展开能看到消费等信息"
// ★★ 卡的**展开态补充行**（用户 2026-10-17 二次修订）：
//   WS / WS2(预留 WS02) / SRC(直推兜底+多久前) / 三个窗口「已用」带进度条 / EGRESS 只留检查时刻
check("SERVICE：展开行 RES · EGR · USED（**没有第二个工作区时不留 WS2/MO 噪音**，用户 2026-10-21）",
  // ★ 当前工作区并入**标题**；WS2 只在真有第二个 GO 工作区时出现（夹具里那个没 GO ⇒ 不出现 ✓）
  !/row more"><span class="k">WS<\/span>/.test(svc) &&
  !/row more"><span class="k">WS2<\/span>/.test(svc) &&
  !/row more"><span class="k">MO<\/span>/.test(svc) &&
  /row more"><span class="k">RES<\/span><span class="v">WIN</.test(svc) &&          // RES=来源，WIN=推送
  /row more"><span class="k">EGR<\/span>/.test(svc) &&
  /row more"><span class="k">USED<\/span><span class="v">0\.4 - 18\.6 - 42\.5</.test(svc) &&
  !/class="bar more/.test(svc) &&
  !/\$12|\$30|\$60/.test(svc) &&
  // 顺序：RES(WIN) → EGR(检查时刻) → USED —— 用唯一值做标记
  (() => {
    const i = (k) => svc.indexOf(k);
    const t = ">2026-10-17 02:23:19<";
    return i(">WIN<") < i(t) && i(t) < i(">USED<");
  })() &&
  !/cookie/.test(svc) && !/>SRC</.test(svc) && !/直推/.test(svc) &&
  !/端点/.test(svc) && !/连续失败/.test(svc) &&
  !/<span class="k">出口</.test(svc), "OCGO 展开");
check("SERVICE：有第二个 GO 工作区时才出现 WS2 行，且写**它的真实标识**（不写死 WS02 ✗）",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.workspaces = [
      { id: "wrk_A", name: "主账号", label: "主账号", source: "cookie（按工作区）", has_go: true,
        level: "ok", meters: [
          { window: "fiveHour", short: "5H", usable_percent: 80, remaining_usd: 9.6, used_usd: 2.4, limit_usd: 12, level: "ok" },
          { window: "month", short: "月", usable_percent: 70, remaining_usd: 42, used_usd: 18, limit_usd: 60, level: "ok" }] },
      { id: "wrk_B", name: "备账号", label: "备账号", source: "cookie（按工作区）", has_go: true,
        level: "ok", meters: [
          { window: "month", short: "月", usable_percent: 55, remaining_usd: 33, used_usd: 27, limit_usd: 60, level: "ok", resets_at_ms: Date.now() + 12 * 86400000 }] },
    ];
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.setTabIndex(0);                 // ★ 必须在 SERVICE 页（否则标题不展开 ✗，前面用例把它停在别页了）
    t.renderService();
    const h = els["svc-body"].innerHTML;
    // 选哪个当"当前"由 svcPick 决定 ⇒ 不硬编码顺序，只要求"两个名字一个在标题、一个在 WS2"
    const titleM = (h.match(/class="mname">OCGO ([^<]*)/) || [])[1] || "";
    const ws2M = (h.match(/class="k">WS2<\/span><span class="v">([^<]*)/) || [])[1] || "";
    const pair = (titleM === "主账号" && ws2M === "备账号") ||
                 (titleM === "备账号" && ws2M === "主账号");
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: SVC_FRAME });
    // ★ MO 行（"不被使用的账号"）：值 = `$剩余 百分比`，空挡 = `(Nd)` 刷新倒计时 ✓
    return pair && /class="k">MO<\/span><span class="v">\$33\.00 55%/.test(h) &&
           /class="k">MO<\/span>[\s\S]{0,120}?class="extra">\(12d\)/.test(h);
  })(), "双账号");
check("★ SERVICE：**当前账号由 `in_use` 决定**（不许再按\"剩余最少\"挑 —— 轮转时剩得少的不等于正在用的 ✗）",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    // A 剩得多、B 剩得少 ⇒ 老规则会挑 B；但 B 没在用 ⇒ 标题必须是 A ✓
    F.workspaces = [
      { id: "wrk_A", name: "在用账号", label: "在用账号", in_use: true, source: "key（账号级，不分工作区）",
        has_go: true, level: "ok", meters: [
          { window: "fiveHour", short: "5H", usable_percent: 90, remaining_usd: 10.8, used_usd: 1.2, limit_usd: 12, level: "ok" }] },
      { id: "wrk_B", name: "备着", label: "备着", in_use: false, source: "key（账号级，不分工作区）",
        has_go: true, level: "ok", meters: [
          { window: "fiveHour", short: "5H", usable_percent: 10, remaining_usd: 1.2, used_usd: 10.8, limit_usd: 12, level: "ok" }] },
    ];
    const picked = t.svcPick(F);
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.setTabIndex(0); t.renderService();
    const h = els["svc-body"].innerHTML;
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: SVC_FRAME });
    return !!picked && picked.id === "wrk_A" &&
           /class="mname">OCGO 在用账号</.test(h) &&
           /class="k">WS2<\/span><span class="v">备着</.test(h);
  })(), "in_use 优先");
check("★ SERVICE：探针的占位名 `（按 Key 直查）` **绝不显示**（用户 2026-10-22「不要按key直接查」）",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.workspaces = [{ id: "wrk_x", name: "（按 Key 直查）", label: "（按 Key 直查）",
      source: "key（账号级，不分工作区）", has_go: true, level: "ok", meters: [
        { window: "fiveHour", short: "5H", usable_percent: 100, remaining_usd: 12,
          used_usd: 0, limit_usd: 12, level: "ok" }] }];
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.setTabIndex(0); t.renderService();
    const h = els["svc-body"].innerHTML;
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: SVC_FRAME });
    // 占位名被忽略 ⇒ 落到 source 判断（key 级）⇒ 显示 `OCGO KEY` ✓，绝不出现"按 Key 直查" ✗
    return !/按\s*Key\s*直查/.test(h) && /class="mname">OCGO KEY</.test(h);
  })(), "屏蔽占位名");
check("SERVICE：标题工作区**取真名**（`OCGO <name>`），不要写死 WS01 ✗（用户 2026-10-21）",
  (() => {
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: SVC_FRAME });
    t.setTabIndex(0); t.renderService();
    const wide = els["svc-body"].innerHTML;
    t.setTabIndex(3); t.renderService();
    const narrow = els["svc-body"].innerHTML;
    // 夹具里第一个工作区 label="Default" ⇒ 展开显示 `OCGO Default` ✓，收起只 `OCGO` ✓
    return /class="mname">OCGO Default</.test(wide) && /class="mname">OCGO</.test(narrow) &&
           !/WS01/.test(wide) && !/WS01/.test(narrow);
  })(), "标题带工作区");
check("SERVICE：只有账号级 Key（没 Cookie）时标题写 **KEY**，不假装是某个工作区 ✗",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.workspaces = [{ id: "wrk_EXAMPLE000000000000000000", name: "", label: "wrk_EXAMPLE000000000000000000",
                      source: "key（账号级，不分工作区）", has_go: true, level: "ok", meters: [
      { window: "fiveHour", short: "5H", usable_percent: 99.76, remaining_usd: 11.97, used_usd: 0.029, limit_usd: 12, level: "ok" }] }];
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.setTabIndex(0);
    t.renderService();
    const h = els["svc-body"].innerHTML;
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: SVC_FRAME });
    return /class="mname">OCGO KEY</.test(h) && !/WS01/.test(h);
  })(), "KEY 标识");
check("★ 修 bug：svc-node 卡**键盘/鼠标选中也要展开**（原来只写了 :hover ⇒ 选中没更多信息 ✗）",
  /\.svc\.focus \.card\.svc-node \.row\s*\{\s*display:\s*grid/.test(html), "选中展开");
check("★ 收起态核心行**单行省略**（不再折成 5 行 ✗；用户 2026-10-17 二次修正）",
  /\.svc \.card\.svc-node \.row\.is-core \.v\s*\{[^}]*white-space:\s*nowrap/.test(html), "单行");
check("★ 小字（右侧空挡 .extra）必须真的能显示：svc-node 的行要**三列**",
  /\.svc \.card\.svc-node \.row\s*\{[^}]*grid-template-columns:\s*auto 1fr auto/.test(html), "三列");
check("★ 数据来源语义：**键 RES、值 WIN（电脑推送）/ TOK（本机 token 拉取）**",
  /svcRow\("RES", frame\.fallback \? "TOK" : "WIN"/.test(code), "RES=来源");
check("★ WECHAT / AGENT 收起态 = **一行核心**（次要信息塞右侧空挡，省一行）",
  (() => {
    t.nodes.set("svc-agent", { node: "svc-agent", stale_seconds: 5, frame: {
      schema: "qyn.svc-agent/1", level: "ok", headline: "最后一轮 18:03",
      generated_at_ms: Date.now(), fresh_for_ms: 300000,
      items: [{ key: "微信通道", value: "接收中 · 最后 10/17 02:15 text", level: "ok" },
              { key: "最后一轮", value: "18:03 message · 退出码 0 · 63s", level: "ok" },
              { key: "唤醒链", value: "下一个 10-17 11:00（下一块 09:00-11:00）", level: "ok" }] } });
    const coreW = (its, wide) => {
      const ch = its.find(i => i.key === "微信通道") || {};
      const v = String(ch.value || "");
      const stat = v.split(" · ")[0] || "—";
      const t2 = (v.match(/\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2}/) || v.match(/\d{1,2}:\d{2}/) || [""])[0];
      // ★ 用户 2026-10-19：RX 与 state 对调；且**收起只给时刻**，展开才带日期
      const hm2 = (t2.match(/\d{1,2}:\d{2}$/) || [""])[0];
      const rxVal = wide ? (t2 || hm2) : (hm2 || t2);
      return [{ k: "RX", v: rxVal || stat, level: ch.level,
                exk: wide ? "STAT" : "", exv: wide ? stat : "" }];
    };
    const coreA = (its, wide) => {
      const r = its.find(i => i.key === "最后一轮") || {};
      const w = its.find(i => i.key === "唤醒链") || {};
      const t3 = (String(r.value || "").match(/\d{1,2}:\d{2}/) || [""])[0];
      const nx = (String(w.value || "").match(/\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}/) || [""])[0];
      return [{ k: "LAST", v: t3 || "—", level: r.level,
                exk: (wide && nx) ? "NEXT" : "", exv: (wide && nx) ? nx : "" }];
    };
    // ① 大模块展开（SERVICE 是当前面板）⇒ 右侧空挡有 RX / NEXT
    t.setTabIndex(0);
    t.renderSvcNode("svc-wechat", "svc-agent", "WECHAT", { keys: ["微信通道"], core: coreW });
    t.renderSvcNode("svc-agent", "svc-agent", "AGENT", { exclude: ["微信通道"], core: coreA });
    const w1 = els["svc-wechat"].innerHTML, a1 = els["svc-agent"].innerHTML;
    const oneRow = (h) => (h.match(/class="row is-core"/g) || []).length === 1;
    // ★ 只取**核心那一行**来判断：详情行也在这个 HTML 里（只是 CSS 藏着），
    //   全局匹配「有没有日期」会误判 ✗（踩过一次）
    const coreRow = (h) => (h.match(/<div class="row is-core">[\s\S]*?<\/div>/) || [""])[0];
    const cw1x = coreRow(w1);
    const okWide = oneRow(w1) && />RX</.test(cw1x) && />10\/17 02:15</.test(cw1x) &&
                   /class="extra"><span class="k">STAT<\/span><span class="v">接收中</.test(cw1x) &&
                   oneRow(a1) && />LAST</.test(a1) && />18:03</.test(a1) &&
                   /class="extra"><span class="k">NEXT<\/span><span class="v">10-17 11:00</.test(a1);
    // ② 切到别的面板（SERVICE 不再展开）⇒ 右侧空挡应为空；
    //    **且 RX 只留时刻、不带日期**（用户 2026-10-19："缩起时只能看到日期了" ✗）
    t.setTabIndex(3);
    t.renderSvcNode("svc-wechat", "svc-agent", "WECHAT", { keys: ["微信通道"], core: coreW });
    const w2 = els["svc-wechat"].innerHTML;
    const cw2 = coreRow(w2);
    const okNarrow = oneRow(w2) && /class="extra"><\/span>/.test(cw2) &&
                     />RX</.test(cw2) && />02:15</.test(cw2) &&
                     !/10\/17/.test(cw2) && !/>STAT</.test(cw2);
    return okWide && okNarrow;
  })(), "核心一行 + 空挡");
check("★ WECHAT：**收起只给时刻、展开才带日期**（RX 与 state 对调，用户 2026-10-19）",
  /return \[\{ k: "RX", v: rxVal \|\| stat, level: ch\.level,/.test(code) &&
  /const rxVal = wide \? \(t \|\| hm\) : \(hm \|\| t\);/.test(code) &&
  /exk: wide \? "STAT" : "", exv: wide \? stat : ""/.test(code), "时刻/日期分野");
check("CSS：核心信息行（.is-core）常显，其余要悬停/选中才展开",
  /\.svc \.card\.svc-node \.row\.is-core\s*\{\s*display:\s*grid/.test(html), "常显");
check("★ bug：收起态下「已用」那三条**不许**跟着显示（曾被 .panel.collapsed .svc .bar 盖掉）",
  /\.panel\.collapsed \.svc \.bar\.more\s*\{\s*display:\s*none/.test(html) &&
  /\.panel \.svc\.focus \.bar\.more\s*\{\s*display:\s*block/.test(html), "收起不显示");
check("SERVICE：备用 WS 有**剩余月用量 + 刷新天数**（放在名称下面一行，键名 MO）",
  /svcResetDays\(ws2Month\.resets_at_ms\)/.test(code) &&
  /svcRow\("MO", ws2Line/.test(code) &&
  /usable_percent/.test(code), "备用 WS 月用量");
check("SERVICE：DS API 展开行 = 消费信息（充值 / 赠送 / 低额线）",
  (() => {
    const d = els["svc-ds"].innerHTML;   // DS API 卡住在独立容器里（用户 2026-10-17 拆的）
    return /row more"><span class="k">充值</.test(d) && /¥18\.98/.test(d) &&
           /row more"><span class="k">赠送</.test(d) && /¥17\.38/.test(d) &&
           /row more"><span class="k">低额线</.test(d);
  })(), "DS API 展开");
check("CSS：补充行**只跟着选中**（切过去不许全纵向展开；旧规则已删）",
  /\.svc \.row\.more\s*\{\s*display:\s*none/.test(html) &&
  /\.svc\.focus \.row\.more\s*\{\s*display:\s*grid/.test(html), "展开才显示");
check("★ 用户 2026-10-17 二次修正：补充行**只跟着选中**（切过去不许全纵向展开）",
  /\.svc \.row\.more\s*\{\s*display:\s*none/.test(html) &&
  /\.svc\.focus \.row\.more\s*\{\s*display:\s*grid/.test(html) &&
  // 旧规则（"面板展开就露"）必须已经删掉
  !/\.panel\.collapsed \.svc:not\(\.focus\) \.row\.more/.test(html), "只看选中");
check("SERVICE：鼠标也能选中卡（不只是键盘；mouseover→focus、mouseleave→取消）",
  /function bindServiceHover\(\)/.test(code) && /bindServiceHover\(\);/.test(code) &&
  /closest\("\.svc"\)/.test(code), "鼠标可选");
check("SERVICE：复用监视栏那套 class（card / card-head / row / k / v / extra / bar）",
  /class="card (ok|warn|bad|none)"/.test(svc) && /class="bar ?(warn|bad|unknown)?"/.test(svc) &&
  /class="row"/.test(svc) && /class="k"/.test(svc) && /class="v"/.test(svc) &&
  /class="extra"/.test(svc), "风格统一");
check("SERVICE：数字 = **已用**百分比（21/62/71），口径和条一致",
  svc.includes("21%") && svc.includes("62%") && svc.includes("71%") &&
  !svc.includes("剩"), "已用口径");
check("SERVICE：**收起可见**的行只留一个百分比，不带「剩下多少」的文字/金额",
  (() => {
    // 用户 2026-10-17：金额只出现在**展开行**（.row.more）里，收起态不许有 → 先把展开行剥掉再查
    const visible = svc.replace(/<div class="row more">[\s\S]*?<\/div>/g, "");
    return !visible.includes("$") && !/剩/.test(visible);
  })(), "无剩余文案");
check("SERVICE：跳过没有 GO 订阅的工作区（示例账号）", !svc.includes("示例账号"), "跳过");
// ★ 用户 2026-10-15：「open code go 那里我希望有一个剩余刷新时间」。
//   契约的 `meters[].reset_in` 本来就有（"1 小时 30 分后重置"…），但渲染原来只在卡片头
//   用了 meters[0] 的 ⇒ 周/月看不到。这条守门：三个窗口各自都要显示出来。
check("SERVICE：刷新天数 `(Nd)` 只给 WK/MO —— **5H 不显示**（恒为 1d 没意义，用户 2026-10-19）",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.workspaces[0].meters.forEach((m, i) => {
      m.resets_at_ms = Date.now() + (i + 1) * 86400000;     // 1 / 2 / 3 天后刷新
    });
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.renderService();
    const h = els["svc-body"].innerHTML;
    return !h.includes("后重置") && !h.includes("未开始计时") &&
           /<span class="rstd">\(2d\)<\/span>/.test(h) &&            // 周 ✓
           /<span class="rstd">\(3d\)<\/span>/.test(h) &&            // 月 ✓
           !/<span class="rstd">\(1d\)<\/span>/.test(h) &&           // 5H：不显示 ✗
           (h.match(/class="rstd"/g) || []).length === 2 &&
           // ★ 不是小灰字（用户 2026-10-18）：白字 + 放大
           /\.svc \.rstd\s*\{[^}]*color:\s*var\(--fg\)/.test(html) &&
           /\.svc \.rstd\s*\{[^}]*font-size:\s*18px/.test(html) &&
           // 可见性**只跟面板收起/展开**走（不许再挂 `.svc.focus` ✗ —— 那会让天数永不出现）
           /\.panel\.collapsed \.extra\s*\{\s*display:\s*none/.test(html) &&
           !/\.svc\.focus \.rstd/.test(html) &&
           !/\.svc \.rstd\s*\{[^}]*display:\s*none/.test(html);
  })(), "天数 Nd");

check("SERVICE：OCGO 第四行 = 出口通断（OL / 红色闪烁 OF，复用 .blink-bad）",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.balances = [{ account: "deepseek", currency: "CNY", total: 40.09, level: "ok" }];
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.nodes.set("svc-egress", { node: "svc-egress", stale_seconds: 5,
      frame: { level: "ok", headline: "出口通", items: [] } });
    t.renderService();
    const ok = els["svc-body"].innerHTML;
    t.nodes.set("svc-egress", { node: "svc-egress", stale_seconds: 5,
      frame: { level: "bad", headline: "出口断", items: [] } });
    t.renderService();
    const bad = els["svc-body"].innerHTML;
    return ok.includes(">OL<") && !ok.includes(">OF<") &&
           bad.includes(">OF<") && /class="v bad blink-bad"/.test(bad) &&
           // DS API 卡现在住在**独立容器** #svc-ds 里（用户 2026-10-17 拆的）
           els["svc-ds"].innerHTML.includes("DS API") &&
           els["svc-ds"].innerHTML.includes("¥40.09");
  })(), "OL / OF");
check("★ 出口三态（用户 2026-10-19）：抖动 = **黄 OF 不闪**、不把 OCGO 点红；真断才红闪",
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.balances = [];
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    // ① 抖动：探针连续 1~2 次失败 ⇒ level=warn
    t.nodes.set("svc-egress", { node: "svc-egress", stale_seconds: 5, frame: {
      schema: "qyn.svc-egress/1", level: "warn", headline: "出口抖动（连续 2 次）",
      generated_at_ms: Date.now(), fresh_for_ms: 900000,
      items: [{ key: "出口", value: "no", level: "warn" }] } });
    t.renderService();
    const h1 = els["svc-body"].innerHTML;
    const jitter = /class="v warn">OF</.test(h1) &&
                   !/blink-bad">OF</.test(h1) &&
                   /class="card warn/.test(h1);                // OCGO 变**黄**，不是红也不是灰
    // ② 真断：连续 ≥3 次 ⇒ level=critical ⇒ 红闪 + OCGO 变红
    //   ⚠️ 这条同时守住一个**老 bug**：原来把 "bad" 传给 `SVC_CARD`（键是契约等级）
    //      取不到 ⇒ 退化成 `none`（灰），出口真断时卡上的灯却是灰的 ✗
    t.nodes.set("svc-egress", { node: "svc-egress", stale_seconds: 5, frame: {
      schema: "qyn.svc-egress/1", level: "critical", headline: "出口断（连续 3 次）",
      generated_at_ms: Date.now(), fresh_for_ms: 900000,
      items: [{ key: "出口", value: "no", level: "critical" }] } });
    t.renderService();
    const h2 = els["svc-body"].innerHTML;
    const down = /class="v bad blink-bad">OF</.test(h2) && /class="card bad/.test(h2);
    // ③ 抖动不该点亮 MASTER WARNING（只到黄 ⇒ 最多 CAUTION）
    return jitter && down;
  })(), "出口三态");
check("★ WECHAT/AGENT **各算各的等级**（AGENT 的「退出码 1」不许把微信点红，用户 2026-10-19）",
  (() => {
    t.nodes.set("svc-agent", { node: "svc-agent", stale_seconds: 5, frame: {
      schema: "qyn.svc-agent/1", level: "bad", headline: "最后一轮 23:30",
      generated_at_ms: Date.now(), fresh_for_ms: 300000,
      items: [{ key: "最后一轮", value: "23:30 boundary · 退出码 1 · 23s", level: "bad" },
              { key: "微信通道", value: "接收中 · 最后 10/19 20:08 text", level: "ok" },
              { key: "接收方轮询", value: "16 秒前", level: "ok" }] } });
    const coreW2 = (its, wide) => {
      const ch = its.find(i => i.key === "微信通道") || {};
      const v = String(ch.value || "");
      const t4 = (v.match(/\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2}/) || [""])[0];
      return [{ k: "RX", v: t4 || "—", level: ch.level }];
    };
    const coreA2 = (its) => {
      const r = its.find(i => i.key === "最后一轮") || {};
      return [{ k: "LAST", v: "23:30", level: r.level }];
    };
    t.setTabIndex(0);
    t.renderSvcNode("svc-wechat", "svc-agent", "WECHAT",
      { keys: ["微信通道", "接收方轮询"], core: coreW2 });
    t.renderSvcNode("svc-agent", "svc-agent", "AGENT", { exclude: ["微信通道", "接收方轮询"], core: coreA2 });
    const wl = (els["svc-wechat"].innerHTML.match(/class="card svc-node ([a-z]+)"/) || [])[1] || "";
    const al = (els["svc-agent"].innerHTML.match(/class="card svc-node ([a-z]+)"/) || [])[1] || "";
    // 微信那几项全是 ok ⇒ 绿；AGENT 有 bad 项 ⇒ 红（帧整体是 bad，但**不许传染**给微信 ✗）
    return wl === "ok" && al === "bad";
  })(), "分卡等级");
check("★ 输入 bug 三项（用户 2026-10-20）：关对话摘焦点 / 输入框里左右键归输入框 / Esc 只一个监听器",
  (() => {
    // ① chatClose 必须 blur（否则焦点留在隐藏 input 上）
    const closeBody = (code.match(/function chatClose\(\)[\s\S]{0,600}?\n\}/) || [""])[0];
    const blurs = /chat-text/.test(closeBody) && /\.blur\(\)/.test(closeBody);
    // ② 全局监听器必须先判"正在输入"
    const editing = /function isEditing\(e\)/.test(code) &&
                    /if \(isEditing\(e\)\) return;/.test(code);
    const editingBeforeArrows = code.indexOf("if (isEditing(e)) return;") <
                                code.indexOf('if (e.key === "ArrowRight")');
    // ③ 全页只有一个 keydown 监听器
    const oneListener = (code.match(/document\.addEventListener\("keydown"/g) || []).length === 1;
    const escCentralized = /function bindKeys\(\)/.test(code) &&
                           /safeInit\("bindKeys", bindKeys\)/.test(code);
    return blurs && editing && editingBeforeArrows && oneListener && escCentralized;
  })(), "三个输入 bug");
check("SERVICE：进度条等级仍直接读后端 level（条宽读 usable_percent，不自己复算颜色）",
  /svcBar\(pct, m\.level\)/.test(code) && /m\.usable_percent/.test(code) &&
  !/m\.remaining_percent/.test(code), "只渲染");
// 用户 2026-10-15 选定：**填满 = 用光**（条宽 = 100 - usable_percent）
check("SERVICE：进度条填满 = 用光（宽度取 100 - usable_percent）",
  /100 - pct/.test(code) && /width:21\./.test(svc),   // 5H 剩 78.63% ⇒ 条宽 21.37%
  "已用方向");
// 用户 2026-10-15：已用超过 80% ⇒ 条标黄（阈值走 setting，可调）
check("SERVICE：已用 >80% ⇒ 条标黄（阈值 svcUsedWarn 默认 80，不写死）",
  t.setting("svcUsedWarn") === 80 && /used > setting\("svcUsedWarn"\)/.test(code) &&
  (() => {
    const F = JSON.parse(JSON.stringify(SVC_FRAME));
    F.workspaces[0].meters[0].usable_percent = 15;      // 已用 85% > 80%
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: F });
    t.renderService();
    const h = els["svc-body"].innerHTML;
    return /class="bar warn"/.test(h) && h.includes("85%");
  })(), "黄条");
check("SERVICE：没超阈值时不标黄（5H 剩 78.63% ⇒ 已用 21.37% ⇒ 默认色）",
  (() => {
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: SVC_FRAME });
    t.renderService();
    return /class="bar ">/.test(els["svc-body"].innerHTML);
  })(), "默认色");
// 用户 2026-10-18：卡头显示**月刷新**倒计时（不是 5H、也不是"最近一次"）
{
  const SVC4 = JSON.parse(JSON.stringify(SVC_FRAME));
  const mMon = SVC4.workspaces[0].meters.find(m => m.window === "month");
  mMon.resets_at_ms = Date.now() + 18 * 86400000;      // 18 天后刷新月额度
  t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: SVC4 });
  t.renderService();
  const svc4 = els["svc-body"].innerHTML;
  check("SERVICE：卡头倒计时 `(Nd)`（收起为空；白字大字，不许灰小字）",
    /<span class="mnote">(数据陈旧)?<span class="rsth">\(\d+d\)<\/span><\/span>/.test(svc4) &&
    !/<span class="mnote">[^<]*(\d{2}:\d{2}|未开始计时)/.test(svc4) &&
    /\.svc \.rsth\s*\{[^}]*font-size:\s*26px/.test(html) &&
    /\.svc \.rsth\s*\{[^}]*color:\s*var\(--fg\)/.test(html) &&
    /\.svc \.rsth\s*\{\s*display:\s*none/.test(html) &&
    /\.panel:not\(\.collapsed\) \.svc \.rsth\s*\{\s*display:\s*inline/.test(html),
    "卡头倒计时");
  check("SERVICE：收起态仍保留卡头摘要位（只砍等级那格）+ 字号放大",
    /\.panel\.collapsed \.svc \.state\s*\{\s*display:\s*none/.test(html) &&
    /\.panel\.collapsed \.svc \.mnote\s*\{\s*display:\s*block/.test(html) &&
    /\.svc \.mnote\s*\{[^}]*font-size:\s*16px/.test(html) &&
    /\.svc \.card\s*\{[^}]*flex:\s*0 0 auto/.test(html), "收起保留 + 字号更大");
}
// 选择规则：剩余**金额**最少且**不为 0**
const SVC2 = JSON.parse(JSON.stringify(SVC_FRAME));
SVC2.workspaces.push({ label: "B", has_go: true, level: "warn", meters: [
  { window: "fiveHour", short: "5H", usable_percent: 8, remaining_usd: 1.2, level: "warn", reset_in: "x" }] });
SVC2.workspaces.push({ label: "C", has_go: true, level: "critical", meters: [
  { window: "fiveHour", short: "5H", usable_percent: 0, remaining_usd: 0, level: "critical", reset_in: "y" }] });
t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: SVC2 });
t.renderService();
svc = els["svc-body"].innerHTML;
check("SERVICE：选「剩余最少且不为 0」的工作区（B 剩 $1.2 胜 Default $9.44；C 为 0 不选）",
  t.svcPick(SVC2) && t.svcPick(SVC2).label === "B" &&
  (() => {
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: SVC2 });
    t.renderService();
    const h = els["svc-body"].innerHTML;
    return h.includes("92%");                            // B 剩 8% ⇒ 已用 92%
  })(), "选择规则");
check("SERVICE：usable_percent 为 null 时写「无数据」，不许当 0%",
  (() => {
    const SVC3 = JSON.parse(JSON.stringify(SVC_FRAME));
    SVC3.workspaces[0].meters[0].usable_percent = null;
    SVC3.workspaces[0].meters[0].level = "unknown";
    t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 1, frame: SVC3 });
    t.renderService();
    const h = els["svc-body"].innerHTML;
    return h.includes("无数据") && !h.includes('>0%<');   // 文本不许写 0%（条宽 0 是可以的）
  })(), "无数据");
t.nodes.delete("svc-opencode");
t.renderService();
check("SERVICE：没有样本时显示「等 Windows 侧推数据…」",
  els["svc-body"].innerHTML.includes("等 Windows 侧推数据"), "等待");

// ── 通用 svc-* 渲染器（用户 2026-10-16；契约 CENTRAL/GUI/SCREEN-PROBES.md）──────
check("SERVICE：通用 svc-* 渲染器存在，且 svc-agent 已接上",
  /function renderSvcNode\(/.test(code) && /SVC_KNOWN_SCHEMA/.test(code) &&
  /renderSvcNode\("svc-agent"/.test(code) && /id="svc-agent"/.test(html), "通用渲染器");
check("SERVICE：契约兼容性 —— 拆块但**不丢 key** / level 缺了当 ok / 值转义 / 认不得 schema 就降级",
  /const pick = \(opts && opts\.keys\)/.test(code) &&
  /const skip = \(opts && opts\.exclude\)/.test(code) &&
  /renderSvcNode\("svc-agent", "svc-agent", "AGENT",\s*\{ exclude:/.test(code) &&
  /svcCol\(it\.level\)/.test(code) &&
  /svcEsc/.test(code) && /known \? /.test(code), "兼容约定");
check("SERVICE：拆出来的块（WECHAT）用**自己第一项**当摘要，不显示 AGENT 的 headline",
  (() => {
    t.nodes.set("svc-agent", { node: "svc-agent", stale_seconds: 5, frame: {
      schema: "qyn.svc-agent/1", level: "ok", headline: "最后一轮 18:03",
      generated_at_ms: Date.now(), fresh_for_ms: 300000,
      items: [{ key: "微信通道", value: "接收中 · 最后 10/16 18:03 text", level: "ok" },
              { key: "最后一轮", value: "18:03", level: "ok" }] } });
    t.renderSvcNode("svc-wechat", "svc-agent", "WECHAT", { keys: ["微信通道"] });
    const w = els["svc-wechat"].innerHTML;
    return w.includes("接收中 · 最后 10/16 18:03 text") && !w.includes("最后一轮 18:03");
  })(), "WECHAT 摘要");
check("SERVICE：WECHAT / AGENT 两块合起来覆盖全部 items（将来新 key 落 AGENT，不丢）",
  (() => {
    t.nodes.set("svc-agent", { node: "svc-agent", stale_seconds: 5, frame: {
      schema: "qyn.svc-agent/1", level: "ok", headline: "最后一轮 18:03",
      generated_at_ms: Date.now(), fresh_for_ms: 300000,
      items: [{ key: "微信通道", value: "接收中", level: "ok" },
              { key: "最后一条发出", value: "已送达", level: "ok" },
              { key: "入站媒体", value: "3 个", level: "ok" },
              { key: "最后一轮", value: "18:03", level: "ok" },
              { key: "将来才有的字段", value: "x", level: "ok" }] } });
    t.renderSvcNode("svc-wechat", "svc-agent", "WECHAT",
      { keys: ["微信通道", "最后一条发出", "入站媒体"] });
    t.renderSvcNode("svc-agent", "svc-agent", "AGENT",
      { exclude: ["微信通道", "最后一条发出", "入站媒体"] });
    const w = els["svc-wechat"].innerHTML, a = els["svc-agent"].innerHTML;
    const hasRow = (h, k) => h.includes('class="k">' + k + "<");
    return w.includes(">WECHAT<") && hasRow(w, "微信通道") && !hasRow(w, "最后一轮") &&
           a.includes(">AGENT<") && hasRow(a, "最后一轮") && hasRow(a, "将来才有的字段") &&
           !hasRow(a, "微信通道");
  })(), "拆块不丢 key");
check("SERVICE：svc-agent 渲染出卡片 + 新 key 自动多一行 + HTML 被转义",
  (() => {
    t.nodes.set("svc-agent", { node: "svc-agent", stale_seconds: 5, frame: {
      schema: "qyn.svc-agent/1", level: "ok", headline: "最后一轮 12:46",
      generated_at_ms: Date.now(), fresh_for_ms: 300000,
      items: [{ key: "最后一轮", value: "12:46 message · 退出码 0", level: "ok" },
              { key: "将来才有的字段", value: "<b>xss</b>", level: "warn" }] } });
    t.renderSvcNode("svc-agent", "svc-agent", "AGENT");
    const h = els["svc-agent"].innerHTML;
    return h.includes(">AGENT<") && h.includes("最后一轮") && h.includes("将来才有的字段") &&
           h.includes("&lt;b&gt;") && !h.includes("<b>xss</b>");
  })(), "svc-agent");
check("SERVICE：卡头分工 —— **标题不压缩、摘要自己省略**（展开时不再挤成 W…）",
  /\.svc \.mname\s*\{[^}]*flex:\s*0 0 auto/.test(html) &&
  /\.svc \.mnote\s*\{[^}]*text-overflow:\s*ellipsis/.test(html) &&
  /\.svc \.card\.svc-node \.mname\s*\{[^}]*flex:\s*0 0 auto/.test(html), "标题不压缩");
check("CSS：卡片内容不许出框（overflow:hidden 兜底）+ 内距收到 6px 给内容腾地方",
  /\.card\s*\{[^}]*overflow:\s*hidden/.test(css) &&
  /\.card\s*\{[^}]*padding:\s*6px 12px/.test(css) &&
  /\.card\s*\{[^}]*gap:\s*4px/.test(css), "不出框");
check("SERVICE：收起态**也留摘要**（机器卡那条 .panel.collapsed .mnote{display:none} 不能波及它）",
  /\.panel\.collapsed \.svc \.mnote\s*\{\s*display:\s*block/.test(html), "收起留摘要");
check("布局：底部对话条高度收到**三分之二**（64 → 43px）",
  /#dock\s*\{[^}]*flex:\s*0 0 43px/.test(html), "43px");
check("SERVICE：.svc 容器按内容定高（flex:1 会让三个容器平分整栏 ⇒ 卡片溢出压到下一块）",
  /\.svc\s*\{\s*flex:\s*0 0 auto/.test(html) && !/\.svc\s*\{\s*flex:\s*1/.test(html), "按内容定高");
check("SERVICE：通用卡**小而密**（值单行省略 + 键列自适应 + 字号小）",
  /\.svc \.card\.svc-node \.row \.v\s*\{[^}]*white-space:\s*nowrap/.test(html) &&
  /\.svc \.card\.svc-node \.row \.v\s*\{[^}]*text-overflow:\s*ellipsis/.test(html) &&
  /\.svc \.card\.svc-node \.row\s*\{[^}]*grid-template-columns:\s*auto 1fr auto/.test(html) &&
  /class="card svc-node/.test(code), "小而密");
check("★ 渲染节奏：兜底整屏重画 ≤1.5s（5s 会让新行最多滞后 5 秒 ✗）",
  /setInterval\(render, 1500\)/.test(code) && !/setInterval\(render, 5000\)/.test(code), "1.5s");
check("★ P1 修复：出口探针**陈旧**时必须报红闪 OF（只按 level 看会一直显示 OL ✗）",
  (() => {
    const mk = (staleSec, genAgo) => {
      // 先给 OCGO 一份能画的帧（出口那行是画在 OCGO 卡里的 ✗ 没有它就没有 EGR 行）
      t.nodes.set("svc-opencode", { node: "svc-opencode", stale_seconds: 2, frame: {
        schema: "apiusage.panel/1", generated_at_ms: Date.now(), fresh_for_ms: 300000,
        level: "ok", workspaces: [{ label: "Default", has_go: true, level: "ok", meters: [
          { window: "fiveHour", short: "5H", usable_percent: 78.63, remaining_usd: 9.44, level: "ok" }] }] } });
      t.nodes.set("svc-egress", { node: "svc-egress", stale_seconds: staleSec, frame: {
        schema: "qyn.svc-egress/1", level: "ok", headline: "出口通",
        generated_at_ms: Date.now() - genAgo, fresh_for_ms: 900000,
        items: [{ key: "检查时刻", value: "2026-10-17 02:23:19" }] } });
      t.renderService();
      return els["svc-body"].innerHTML;
    };
    const good = mk(60, 60000);                    // 新鲜 ⇒ OL
    const dead = mk(3600, 3600000);                // 探针停了一小时 ⇒ 必须 OF
    return /class="v ok">OL</.test(good) && /blink-bad">OF</.test(dead) && !/class="v ok">OL</.test(dead);
  })(), "陈旧 ⇒ OF");
check("★ 在线/掉线统一用 OL / OF（不再写「实时推送」「轮询」这种词）",
  /"OL · 实时推送"/.test(code) && /"OF · 轮询"/.test(code), "OL/OF");
check("★ 惯例：**核心行**值大字（22px）、键小字（13px）；**明细行**值小而密（15px）",
  /\.svc \.card\.svc-node \.row\.is-core \.v\s*\{[^}]*font-size:\s*22px/.test(html) &&
  /\.svc \.card\.svc-node \.row \.k\s*\{[^}]*font-size:\s*13px/.test(html) &&
  /\.svc \.card\.svc-node \.row \.v\s*\{[^}]*font-size:\s*15px/.test(html), "核心大写/明细小");
check("SERVICE：通用卡平时只留卡头一行 + 有问题的行，明细要悬停才展开",
  /\.svc \.card\.svc-node \.row\s*\{\s*display:\s*none/.test(html) &&
  /\.svc \.card\.svc-node \.row\.is-bad\s*\{\s*display:\s*grid/.test(html) &&
  /\.svc \.card\.svc-node:hover \.row\s*\{\s*display:\s*grid/.test(html) &&
  /is-bad/.test(code), "平时一行");
check("SERVICE：卡头带帧自己的 headline（契约：折叠态就显示它）",
  /const note = stale \?/.test(code) && /svcEsc\(note\)/.test(code), "headline");
check("SERVICE：认不得的 schema ⇒ 整块降级只显示 headline",
  (() => {
    t.nodes.set("svc-agent", { node: "svc-agent", stale_seconds: 5, frame: {
      schema: "qyn.svc-agent/99", level: "warn", headline: "契约变了",
      generated_at_ms: Date.now(), fresh_for_ms: 300000,
      items: [{ key: "不该显示", value: "x", level: "ok" }] } });
    t.renderSvcNode("svc-agent", "svc-agent", "AGENT");
    const h = els["svc-agent"].innerHTML;
    return h.includes("契约已更新") && h.includes("契约变了") && !h.includes("不该显示");
  })(), "降级");

// 用户 2026-10-15：取消选中后必须能滚回顶部（原来会卡在半途）
t.setAgent({ ok: true, top_done: false, rules: [], warnings: [], unscheduled: [],
  plan: { slots: [{ date: "2026-10-15", time: "21:00-22:30", task: "示例课程C 作业3",
                    why: "晚饭后第一块", gist: "Pxx" }] } });
t.setTabIndex(4);                       // AGENT 展开（索引 4；3 是 TIME）
t.setFocus("aslots");
t.renderAgent();
els["box-aslots"].scrollTop = 250;      // 假装滚到中间
t.setFocus(null);                       // 取消选中
t.renderAgent();
check("子框：取消选中后滚回顶部（不再卡在半途）",
  els["box-aslots"].scrollTop === 0, `scrollTop=${els["box-aslots"].scrollTop}`);
t.setFocus(null);
t.renderAgent();

// 用户 2026-10-15 报的两条：AGENT 依据选不中、软偏好会消失
check("AGENT：悬停绑定覆盖**两个**容器（只绑 TIME 时 AGENT 鼠标选不中）",
  /bindBoxHover\("time-cards"\)/.test(code) && /bindBoxHover\("agent-cards"\)/.test(code),
  "双容器");
check("AGENT：软偏好块在正常路径**永不隐藏**（没有内容也显示「没有」）",
  /emptyWarn/.test(code) && /setBox\("awarn", awarnLv \+ " agent-warn"/.test(code), "不消失");

// ★★★ 灯（lamp）系统 —— 用户 2026-10-18：飞机驾驶舱逻辑，一套可复用的模块化点灯
check("★ CSS：灯只有**一处**映射（组件都消费 var(--lamp)，不再各自写颜色）",
  /\.ok\s*\{[^}]*--lamp:\s*var\(--ok\)/.test(css) &&
  /\.warn\s*\{[^}]*--lamp:\s*var\(--warn\)/.test(css) &&
  /\.bad\s*\{[^}]*--lamp:\s*var\(--bad\)/.test(css) &&
  /\.off\s*\{[^}]*--lamp:\s*var\(--lamp-dark\)/.test(css) &&
  /\.none\s*\{[^}]*--lamp:\s*var\(--lamp-off\)/.test(css) &&
  (css.match(/border-left:\s*5px solid var\(--lamp, var\(--faint\)\)/g) || []).length === 3 &&
  !/\.card\.ok\s*\{/.test(css) && !/\.netcard\.ok\s*\{/.test(css) && !/\.tcard\.ok\s*\{/.test(css),
  "单一映射");
check("★ 灯：各模块都登记（机器卡 / 网卡 / WAN / 子框 / SERVICE 四块）",
  /lightSet\("dc:" \+ m\.id/.test(code) &&
  /lightSet\("net:" \+ m\.id/.test(code) &&
  /lightSet\("net:wan"/.test(code) &&
  /lightSet\("box:" \+ kind/.test(code) &&
  /lightSet\("svc:ocgo"/.test(code) &&
  /lightSet\("svc:egress"/.test(code) &&
  /lightSet\("svc:ds"/.test(code) &&
  /lightSet\("svc:" \+ hostId/.test(code), "模块登记");
check("★ 灯：汇总取最坏，且**离线（off）不参与告警**（没电 ≠ 故障）",
  (() => {
    t.LIGHT_MODULES.clear();
    t.lightSet("a", "A", "ok");
    t.lightSet("b", "B", "off");
    const s1 = t.lightSummary();
    const okOnly = s1.worst === "ok" && !s1.masterWarning && !s1.masterError;
    t.lightSet("c", "C", "warn");
    const s2 = t.lightSummary();
    const caution = s2.worst === "warn" && s2.masterError === true && s2.masterWarning === false;
    t.lightSet("d", "D", "bad");
    const s3 = t.lightSummary();
    const warning = s3.worst === "bad" && s3.masterWarning === true && s3.masterError === false;
    return okOnly && caution && warning;
  })(), "汇总/主灯");
check("★ 灯：掉帧的模块（超过 10s 没登记）按**不亮**看待",
  (() => {
    t.LIGHT_MODULES.clear();
    t.lightSet("x", "X", "bad");
    t.LIGHT_MODULES.get("x").ts = Date.now() - (t.LIGHT_STALE_MS + 1000);
    const s = t.lightSummary();
    return s.worst === "ok" && !s.masterWarning && s.off.length === 1;
  })(), "掉帧");
check("★ 驾驶舱语义：**没问题就是绿灯**（异常 0 条 / 规划依据 / 软偏好都没有）",
  (() => {
    t.setTime(Object.assign({}, TIME_OK, { anomalies: { anomalies: [] } }));
    t.setTabIndex(0);
    t.renderTime();
    const alertCls = els["box-alert"].className;
    const alertGreen = /\bok\b/.test(alertCls) && !/\b(warn|bad|none)\b/.test(alertCls);
    t.setAgent({ ok: true, top_done: false,
                 rules: [{ body: "晚上不排课", kind: "constraint", source: "用户" }],
                 warnings: [], unscheduled: [], plan: { slots: [] } });
    t.renderAgent();
    const rulesCls = els["box-arules"].className;
    const warnCls = els["box-awarn"].className;
    return alertGreen && /\bok\b/.test(rulesCls) && /\bok\b/.test(warnCls);
  })(), "绿即正常");
check("★ 驾驶舱语义：碰软偏好 = 黄；**排不上（有 DDL 没位置）= 红**",
  (() => {
    t.setAgent({ ok: true, rules: [],
                 warnings: [{ date: "10-18", slot: "09:00", task: "x", kind: "碰偏好" }],
                 unscheduled: [], plan: { slots: [] } });
    t.renderAgent();
    const yellow = /\bwarn\b/.test(els["box-awarn"].className);
    t.setAgent({ ok: true, rules: [], warnings: [],
                 unscheduled: [{ task: "y", why: "今天排不下" }], plan: { slots: [] } });
    t.renderAgent();
    const red = /\bbad\b/.test(els["box-awarn"].className);
    t.setAgent({ ok: true, rules: [], warnings: [], unscheduled: [], plan: { slots: [] } });
    t.renderAgent();
    return yellow && red;
  })(), "黄/红分级");

// ★★★ 主警告 / 主错误（用户 2026-10-18；样式照波音 MASTER WARNING / MASTER CAUTION）
check("★ 主灯：右下角两个按钮（MASTER WARNING / MASTER CAUTION）+ 黑透镜未点亮 + **已放大**",
  /id="m-warning"[^>]*>MASTER WARNING</.test(html) &&
  /id="m-caution"[^>]*>MASTER CAUTION</.test(html) &&
  /#masters\s*\{[^}]*position:\s*fixed;[^}]*right:\s*76px/.test(html) &&
  /\.master\s*\{[^}]*height:\s*42px/.test(html) &&                    // 放大（原 34px）
  /\.master\s*\{[^}]*font:\s*600 14px/.test(html) &&                  // 字号 12 → 14
  /\.master\s*\{[^}]*color:\s*#39424d/.test(html) &&                  // 未点亮 = 暗刻字
  /\.master\.warning\.lit\s*\{[^}]*color:\s*#ff6b5e/.test(html) &&    // 红 = warning
  /\.master\.caution\.lit\s*\{[^}]*color:\s*#ffc447/.test(html) &&    // 琥珀 = caution
  // ★ 用户 2026-10-18：墙上没人悬停 ⇒ 被按掉的项数必须**画在按钮上**
  /id="m-warning-n"/.test(html) && /id="m-caution-n"/.test(html) &&
  /\.master \.mk\s*\{[^}]*margin-left/.test(html) &&
  /\.master \.mk:empty\s*\{[^}]*display:\s*none/.test(html) &&
  /\.master\.muted\s*\{[^}]*opacity/.test(html), "主灯样式");
check("★ 主灯：只读 lightSummary() + 按下**屏蔽最近一个致因**（因果号 since）",
  /function renderMasters\(\)\s*\{\s*const s = lightSummary\(\);/.test(code) &&
  /LIGHT_SUPPRESSED\.set\(best\.id, best\.since\)/.test(code) &&
  /prev\.state === s && prev\.since/.test(code) &&                    // 心跳不许冲掉因果号
  /safeInit\("bindMasters", bindMasters\)/.test(code), "主灯接线");
check("★ 主灯行为：bad ⇒ 红亮；只有 warn ⇒ 只黄亮；全绿 / 离线 ⇒ 两盏都暗",
  (() => {
    t.LIGHT_MODULES.clear(); t.LIGHT_SUPPRESSED.clear();
    t.lightSet("x1", "模块A", "bad");
    t.renderMasters();
    const red = /\blit\b/.test(els["m-warning"].className) && !/\blit\b/.test(els["m-caution"].className);
    t.LIGHT_MODULES.clear(); t.LIGHT_SUPPRESSED.clear();
    t.lightSet("y1", "模块C", "warn");
    t.renderMasters();
    const amber = !/\blit\b/.test(els["m-warning"].className) && /\blit\b/.test(els["m-caution"].className);
    t.LIGHT_MODULES.clear(); t.LIGHT_SUPPRESSED.clear();
    t.lightSet("y2", "模块D", "ok");
    t.lightSet("y3", "模块E", "off");
    t.renderMasters();
    const dark = !/\blit\b/.test(els["m-warning"].className) && !/\blit\b/.test(els["m-caution"].className);
    return red && amber && dark;
  })(), "主灯颜色");
check("★ 主灯：按一次只屏蔽**最近一个**；再按屏蔽下一个；全按完就灭",
  (() => {
    t.LIGHT_MODULES.clear(); t.LIGHT_SUPPRESSED.clear();
    t.lightSet("a", "A", "bad");
    t.lightSet("b", "B", "bad");        // 更晚 ⇒ 算"最近"
    const two = t.lightSummary().bad.length === 2 && t.lightSummary().masterWarning;
    t.lightSuppressNewest("bad");
    t.renderMasters();
    const s1 = t.lightSummary();
    const oneLeft = s1.bad.filter(m => !m.suppressed).length === 1 && s1.masterWarning === true;
    const hidB = s1.bad.find(m => m.id === "b").suppressed === true;   // 按掉的是**最近的那个**
    const badge1 = els["m-warning-n"].textContent === "1" && /\blit\b/.test(els["m-warning"].className);
    t.lightSuppressNewest("bad");
    t.renderMasters();
    const allOff = t.lightSummary().masterWarning === false;
    // 全被按掉 ⇒ 灯变 muted（暗但**不是"没问题"**）+ 角标写 2
    const muted = /\bmuted\b/.test(els["m-warning"].className) && els["m-warning-n"].textContent === "2";
    return two && oneLeft && hidB && badge1 && allOff && muted;
  })(), "逐次屏蔽");
check("★ 主灯：屏蔽**只隔断这一次因果** —— 状态一变就恢复，新警告照样点亮",
  (() => {
    t.LIGHT_MODULES.clear(); t.LIGHT_SUPPRESSED.clear();
    t.lightSet("a", "A", "bad");
    t.lightSuppressNewest("bad");
    const quiet = t.lightSummary().masterWarning === false;
    t.lightSet("a", "A", "warn");                 // 状态变化 ⇒ 新因果号
    const amber = t.lightSummary().masterError === true;
    t.lightSet("a", "A", "bad");                  // 又红了 ⇒ 红灯重新点亮
    const relit = t.lightSummary().masterWarning === true;
    t.lightSet("c", "C", "bad");                  // 全新模块 ⇒ 也点亮
    const s = t.lightSummary();
    const newOne = s.masterWarning === true && s.bad.filter(m => !m.suppressed).length === 2;
    return quiet && amber && relit && newOne;
  })(), "临时屏蔽");
check("★ SERVICE：状态词统一用 **OL / OF**（用户 2026-10-18：「OL 又变成正常了，改回去」）",
  /SVC_LABEL = \{ ok: "OL", warn: "偏低", critical: "OF", unknown: "无数据" \}/.test(code) &&
  /SVC_NODE_LABEL = \{ ok: "OL", warn: "LOW", critical: "OF", unknown: "—" \}/.test(code) &&
  /SVC_NODE_LABEL\[lv\]/.test(code) &&          // svc-node 卡走节点词表
  !/SVC_LABEL = \{ ok: "正常"/.test(code), "OL/OF 词表");
check("★ 主灯 bug 回归：灯**不许随切面板变**——TIME/AGENT 的子框由每轮 render() 全量刷新",
  /document\.getElementById\("net-out"\)\.innerHTML = internetBlocks\(\);\s*[\s\S]{0,900}?renderTime\(\);\s*renderAgent\(\);\s*renderMasters\(\);/.test(code) &&
  (() => {
    // 造一个 TIME 子框的登记，再把它的时间戳弄旧（模拟"面板一直没切"），
    // 走一遍 render() 之后它必须被刷新，**不能**被判成 off 掉出汇总。
    t.LIGHT_MODULES.clear(); t.LIGHT_SUPPRESSED.clear();
    t.lightSet("box:alert", "alert", "bad");
    t.LIGHT_MODULES.get("box:alert").ts = Date.now() - (t.LIGHT_STALE_MS + 5000);
    const before = t.lightSummary().off.some(m => m.id === "box:alert");
    t.setTime(TIME_OK);                       // 铺好数据，让 render() 里的 renderTime() 有货可渲染
    t.setAgent({ ok: true, rules: [], warnings: [], unscheduled: [], plan: { slots: [] } });
    t.render();
    const s = t.lightSummary();
    const after = s.off.some(m => m.id === "box:alert");
    return before === true && after === false;      // 修好前 before=true,after=true ✗
  })(), "切屏不再抖");
check("CSS：依据/偏好选中时展开明细（.grow 与 .focus 双保险）",
  /\.tcard\.agent-rules\.focus\s+\.tsub/.test(css) &&
  /\.tcard\.agent-warn\.focus\s+\.tsub/.test(css), "展开明细");

// ---------------------------------------------------------------- AGENT 面板（规划）
check("标签页：TABS 有 5 块（三小两大），第 5 块是 AGENT",
  t.TABS.length === 5 && t.TABS[0].id === "panel-service" &&
  t.TABS[4].id === "panel-agent" && t.TIME_TAB_INDEX === 3 && t.AGENT_TAB_INDEX === 4,
  `n=${t.TABS.length} timeIdx=${t.TIME_TAB_INDEX} agentIdx=${t.AGENT_TAB_INDEX}`);
const AGENT_OK = {
  ok: true,
  plan: {
    created_at: "2026-10-14 13:31:48",
    triggered_by: "每日检查 2026-10-14 13:31（重排3：无冲突无警告）",
    focus: "现在 14:20 前写完示例课程预习报告；课后开写示例课程F、示例课程预习报告 挪到周四晚",
    horizon: "2026-10-14 至 2026-10-21", stale: false, age_days: 0,
    slots: [
      { date: "2026-10-15", time: "21:00-22:30", task: "示例课程C 作业3",
        why: "晚饭后第一块，周四 16:30 前当堂交", gist: "Pxx 第 1/2/3 题",
        urgency: "urgent", left: "还剩 6 小时", passed: false },
      { date: "2026-10-15", time: "22:40-23:59", task: "示例课程F",
        why: "", gist: "完整报告", urgency: "normal", left: "还剩 6 天", passed: false },
    ],
  },
  rules: [{ id: "rule-004", kind: "preference", condition: "为任务选择时段时（晚上）",
            body: "晚上尽量不安排任务", source: "用户口述 2026-10-11" }],
  warnings: [{ kind: "violates_rule", rule: "rule-004", date: "2026-10-15",
               slot: "21:00-22:30", task: "示例课程C 作业3" }],
  unscheduled: [],
};
t.setAgent(AGENT_OK);
t.renderAgent();
let ah = agentHtml();
check("AGENT：渲染后四块带**语义类**（CSS 靠它匹配；上一版就是这里对不上）",
  els["box-aplan"].className.includes("agent-plan") &&
  els["box-aslots"].className.includes("agent-slots") &&
  els["box-arules"].className.includes("agent-rules") &&
  els["box-awarn"].className.includes("agent-warn"),
  `aplan=${els["box-aplan"].className} aslots=${els["box-aslots"].className}`);
check("AGENT：时段块默认撑满（.grow）、依据/偏好不撑满",
  els["box-aslots"].className.includes("grow") &&
  !els["box-arules"].className.includes("grow") &&
  !els["box-awarn"].className.includes("grow"), "默认分配");
check("AGENT：显示规划主旨 + 覆盖范围", ah.includes("建议执行列表") && ah.includes("2026-10-14 至 2026-10-21"),
  "规划");
check("AGENT：时段按天分组、带详细任务（gist）",
  ah.includes("示例课程C 作业3") && /class="tday"/.test(ah) && ah.includes("Pxx"), "时段");
// ★ 用户 2026-10-16：「事实和理由分开…理由…一个左箭头放在下一段，展开的时候再阅读」
check("AGENT：理由用**左箭头 + 灰色**、**独立一段**（不再是右箭头同段）",
  ah.includes("← 晚饭后第一块，周四 16:30 前当堂交") && !ah.includes("→ 晚饭后第一块")
  && !ah.includes("为什么排这儿"), "左箭头独立段");
check("AGENT：事实与理由是**两个元素**（理由单独成段，收起时可整段隐掉）",
  /class="agent-gist"[^>]*>[^<]*<\/span>\s*<span class="agent-why"/.test(ah)
  || (/class="agent-why"/.test(ah) && /class="agent-gist"/.test(ah)), "分开");
check("CSS：理由**默认不显示**、展开（.grow/.focus）才显示、整块面板收起时强制隐藏",
  /\.agent-why\s*\{[^}]*display:\s*none/.test(css)
  && /\.tcard\.agent-slots\.grow\s+\.agent-why/.test(css)
  && /\.tcard\.agent-slots\.focus\s+\.agent-why/.test(css)
  && /\.panel\.collapsed\s+\.tcard\.agent-slots\s+\.agent-why/.test(css), "收起不显示理由");
check("CSS：事实（详细任务 gist）**没有被隐藏** —— 收起时也要读得到",
  /\.agent-gist\s*\{[^}]*font-size/.test(css) && !/\.agent-gist[^{]*\{[^}]*display:\s*none/.test(css),
  "事实可见");
check("AGENT：详细任务（gist）走白色样式",
  /class="agent-gist"[^>]*>Pxx/.test(ah), "gist 白");
check("AGENT：紧迫度上色 —— 紧急且 12 小时内 = 红",
  /class="tcourse u-red"[^>]*>[^<]*示例课程C/.test(ah) && ah.includes("紧急 · 12 小时内"), "红");
check("AGENT：紧迫度上色 —— 正常 = 白（u-normal）",
  /class="tcourse u-normal"/.test(ah), "白");
check("AGENT：没有真 why 时用内核的余量兜底（左箭头 + 还剩 X）",
  ah.includes("← 还剩 6 天"), "兜底");
check("AGENT：显示规划依据（规则 + 来源）",
  ah.includes("晚上尽量不安排任务") && ah.includes("用户口述 2026-10-11"), "规则");
check("AGENT：显示软偏好被碰", ah.includes("碰了软偏好 1 处"), "警告");

// ★ 用户 2026-10-15：「这种严重的 DDL 与规划冲突需要标红处理。规划要标红提醒，
//   对应任务 DDL 进入红色闪烁区的时候也要跟着闪烁。」
//   数据由内核 `wall` 给（每块 ddl/ddl_in_minutes/flags，整份 ok/problems），桥透传。
{
  const A2 = JSON.parse(JSON.stringify(AGENT_OK));
  A2.plan.ok = false;
  A2.plan.problems = [{ kind: "past_deadline", task: "示例课程作业", date: "2026-10-16",
                        slot: "19:00-20:00", deadline: "2026-10-16 10:10" }];
  A2.plan.slots = [
    { date: "2026-10-16", time: "19:00-20:00", task: "示例课程作业", why: "", gist: "",
      urgency: "soon", left: "还剩 3 天", passed: false,
      ddl: "2026-10-16 10:10", ddl_in_minutes: 5000, flags: ["past_deadline"] },
    { date: "2026-10-16", time: "21:00-22:00", task: "快到期的活", why: "", gist: "",
      urgency: "urgent", left: "还剩 5 小时", passed: false, ddl_in_minutes: 300, flags: [] },
  ];
  t.setAgent(A2);
  t.renderAgent();
  const h2 = agentHtml();
  check("★ 规划标红：内核 ok=false + problems ⇒ 整块 bad + 闪烁提示",
    els["box-aplan"].className.includes("bad") &&
    /u-red-blink[^>]*>★ 与硬截止冲突 1 处/.test(h2) && h2.includes("示例课程作业"),
    `aplan=${els["box-aplan"].className}`);
  check("★ 排到硬截止之后的那块 = 红 + 闪（不是普通紧迫度色）",
    /class="tcourse u-red-blink"[^>]*>[^<]*示例课程作业/.test(h2) && h2.includes("★ 排到截止之后"),
    "冲突块");
  check("★ DDL 进红色闪烁区（与 DUE 同一档：<6 小时）⇒ 那块跟着闪",
    /class="tcourse u-red-blink"[^>]*>[^<]*快到期的活/.test(h2) && h2.includes("DDL 6 小时内"),
    "跟着闪");
  check("CSS：.u-red-blink 与 DUE 用**同一个闪烁时钟**（不是各自 animation）",
    /body\.flash-on \.u-red-blink/.test(css) &&
    /body\.flash-on \.trow\.red-blink/.test(css), "同一闪法：同一个 body.flash-on");
}
t.setAgent({ ok: false, error: "桥不可用" });
t.renderAgent();
check("AGENT：内核不可用时显示读不到",
  agentHtml().includes("读不到"), "降级");
t.setAgent(AGENT_OK);
t.renderAgent();

// ---------------------------------------------------------------- 推送体系（agent-plan 节点）
// 用户 2026-10-17「改成推送体系」：主路 = **状态墙 SSE** 里 `node == "agent-plan"` 的帧
// （schema `qyn.agent-plan/1`，见 CENTRAL/GUI/SCREEN-PROBES.md）。帧没有 / 过期 / SSE 断开
// ⇒ 退回 `/api/agent`、`/api/time` 轮询（60 秒）。这里不连真墙：直接喂一份 `nodes` 快照
// —— `applySnapshot` 就是页面收到 SSE `snapshot` 事件时走的那条路。
check("推送：复用**同一条**状态墙 SSE（不新开第二条连接）",
  (code.match(/new EventSource/g) || []).length === 1, "一条连接");
check("推送：兜底轮询放宽到 60 秒（推送是主路，轮询只兜底）",
  /setInterval\(loadTime, POLL_MS\)/.test(code) && /setInterval\(loadAgent, POLL_MS\)/.test(code) &&
  /const POLL_MS = 60000/.test(code) &&
  !/setInterval\(loadAgent, 20000\)/.test(code) && !/setInterval\(loadTime, 30000\)/.test(code),
  "60s");

// 兜底那份（/api/agent 与 /api/time）—— 故意和推送帧**用不同的字串**，才好断言"用的哪一份"
const FALLBACK_AGENT = {
  ok: true, top_done: true, top_task: "兜底·最上面那条",
  plan: { created_at: "2026-10-17 23:00:00", triggered_by: "兜底触发",
          focus: "兜底规划主旨", horizon: "2026-10-18 至 2026-10-25",
          stale: false, ok: true, problems: [],
          slots: [{ date: "2026-10-18", time: "09:00-10:00", task: "兜底专属·任务",
                    why: "兜底理由", gist: "兜底要点", urgency: "normal", left: "还剩 3 天" }] },
  rules: [{ id: "rule-9", kind: "preference", body: "兜底专属·规则", source: "用户口述" }],
  warnings: [], unscheduled: [],
};
const FALLBACK_TIME = {
  ok: true,
  sessions: {
    now: { date: "2026-10-17", time: "23:00", weekday: "六", phase: "深夜" },
    sessions: [{ date: "2026-10-18", time: "08:00-09:40", course: "兜底专属·课程",
                 location: "示例教室X1" }],
    due: [{ task: "兜底专属·待办", why: "还剩 5 小时", gist: "兜底描述" }],
  },
  anomalies: { clean: false, anomalies: [{ severity: "low", task: "兜底专属·异常", detail: "细节" }] },
};
// 一份**合法**的推送帧（形状照契约逐字写，一个字段都不发明）
const PUSH_FRESH = {
  schema: "qyn.agent-plan/1",
  generated_at_ms: Date.now(),
  fresh_for_ms: 300000,
  level: "ok",
  headline: "10/18 周日 · 下一块 08:30-11:50 推送专属·示例课程H 报告",
  plan: { created_at: "2026-10-18 00:10:00", stale: false,
          focus: "推送专属·规划主旨", order: ["推送专属·示例课程H 报告"],
          slots: [{ date: "2026-10-18", time: "08:30-11:50",
                    task: "推送专属·示例课程H 第二次实验报告", why: "上午整块", passed: false }],
          problems: [] },
  time: { now: { date: "2026-10-18", time: "00:10", weekday: "日", phase: "凌晨" },
          // ★ lead_days 只作排序信号：DDL 那行**只显示时间**（不能出现"需提前 N 天"）
          due: [{ task: "推送专属·待办A", date: "2026-10-18", why: "还剩 5 小时",
                  urgency: "soon", lead_days: 3 }],
          sessions: [{ date: "2026-10-18", time: "14:00-15:40",
                       course: "推送专属·示例课程B", location: "示例教室X1",
                       kind: "normal", water: true }] },
};

t.setAgentFallback(FALLBACK_AGENT);
t.setTimeFallback(FALLBACK_TIME);
t.setSseLive(true);
t.applySnapshot({ nodes: [{ node: t.PLAN_NODE, frame: PUSH_FRESH }] });
check("推送：帧新鲜 ⇒ 采纳推送（AGENT 面板画 plan.slots）",
  agentHtml().includes("推送专属·示例课程H 第二次实验报告") &&
  agentHtml().includes("推送专属·规划主旨") &&
  !agentHtml().includes("兜底专属·任务"), "用推送");
check("推送：时段理由仍是「左箭头 + 独立段」（渲染口径不变）",
  agentHtml().includes("← 上午整块") && !agentHtml().includes("→ 上午整块"), "左箭头");
check("推送：DUE 画 time.due，红色闪烁阈值仍取自 why（还剩 5 小时 ⇒ 红闪）",
  timeHtml().includes("推送专属·待办A") && timeHtml().includes("还剩 5 小时") &&
  /class="trow red-blink"/.test(timeHtml()) && !timeHtml().includes("兜底专属·待办"), "DUE");
check("推送：日程画 time.sessions，水课带「（课·水）」标记",
  timeHtml().includes("推送专属·示例课程B（课·水）") &&
  timeHtml().includes("示例教室X1") && timeHtml().includes("14:00-15:40"), "日程");
check("推送：标题栏用帧里 time.now（日期 + 周几 + 时段 + 时刻）",
  els["time-note"].textContent.includes("2026-10-18") &&
  els["time-note"].textContent.includes("日") &&
  els["time-note"].textContent.includes("00:10"), els["time-note"].textContent);
check("推送：DDL 那行只有时间 —— 帧里的 lead_days **绝不显示**成「需提前 N 天」",
  !timeHtml().includes("需提前") && !timeHtml().includes("提前"), "不显示提前");
check("推送：帧里没有异常清单 ⇒ 沿用兜底那份（ALERT 块不许变空）",
  timeHtml().includes("异常 1 条") && timeHtml().includes("兜底专属·异常"), "沿用兜底");
check("推送：帧里没有的规则/重排判据沿用兜底（规划依据不白、按钮判据还在）",
  agentHtml().includes("兜底专属·规则") && agentHtml().includes("2026-10-18 至 2026-10-25") &&
  els["dock-replan"].hidden === false, "沿用兜底");

// ★ 契约：`level=bad`（内核 ok=false / problems 非空）⇒ 与 /api/agent 的 `plan.ok=false` 同口径
const PUSH_BAD = JSON.parse(JSON.stringify(PUSH_FRESH));
PUSH_BAD.generated_at_ms = Date.now() + 1;
PUSH_BAD.level = "bad";
PUSH_BAD.plan.problems = [{ kind: "past_deadline", task: "推送专属·示例课程作业",
                            date: "2026-10-18", slot: "19:00-20:00",
                            deadline: "2026-10-18 10:10" }];
PUSH_BAD.plan.slots = [{ date: "2026-10-18", time: "19:00-20:00", task: "推送专属·示例课程作业",
                         why: "", passed: false }];
t.applySnapshot({ nodes: [{ node: t.PLAN_NODE, frame: PUSH_BAD }] });
check("★ 推送：level=bad ⇒ 规划块标红 + 闪烁 + 点名问题（与 /api/agent 同口径）",
  els["box-aplan"].className.includes("bad") &&
  agentHtml().includes("与硬截止冲突 1 处") && agentHtml().includes("推送专属·示例课程作业") &&
  agentHtml().includes("排不下"), `aplan=${els["box-aplan"].className}`);

// ★ 兜底①：帧**过期**（generated_at_ms + fresh_for_ms 已过）—— 事件不会再来通知，
//   靠 pushGuard 每 2 秒按时间发现 ⇒ 立刻改用 /api/agent、/api/time 的轮询结果。
const PUSH_STALE = JSON.parse(JSON.stringify(PUSH_FRESH));
PUSH_STALE.generated_at_ms = Date.now() - 600000;          // 10 分钟前 > fresh_for_ms（5 分钟）
PUSH_STALE.plan.slots[0].task = "过期帧专属·不该上屏";
PUSH_STALE.time.due[0].task = "过期帧专属·待办";
PUSH_STALE.time.sessions[0].course = "过期帧专属·课程";
const callsBefore = t.fetchCalls().length;
t.applySnapshot({ nodes: [{ node: t.PLAN_NODE, frame: PUSH_STALE }] });
check("推送：帧过期 ⇒ 走兜底（AGENT / DUE / 日程都改用轮询那份）",
  agentHtml().includes("兜底专属·任务") && !agentHtml().includes("过期帧专属") &&
  timeHtml().includes("兜底专属·待办") && !timeHtml().includes("过期帧专属") &&
  t.pushSigOf() === "poll", `sig=${t.pushSigOf()}`);
check("推送：掉回兜底时**立刻**取一次（不等下一次 60 秒）",
  t.fetchCalls().length - callsBefore >= 2, `+${t.fetchCalls().length - callsBefore} 次`);
check("推送：帧没过期时**不**多打桥（推送在线就不轮询）",
  (() => {
    const before = t.fetchCalls().length;
    t.applySnapshot({ nodes: [{ node: t.PLAN_NODE, frame: PUSH_FRESH }] });
    return t.fetchCalls().length === before && t.pushSigOf().indexOf("push:") === 0;
  })(), "不打桥");

// ★ 兜底②：SSE 断开 —— 墙上的旧帧还在 `nodes` 里，但**不能再当现状**（会一直显示同一份）
t.setSseLive(false);
t.pushGuard();
check("推送：SSE 断开 ⇒ 走兜底（墙上的旧帧不再当现状）",
  t.pushSigOf() === "poll" && agentHtml().includes("兜底专属·任务") &&
  !agentHtml().includes("推送专属·示例课程H"), `sig=${t.pushSigOf()}`);
check("推送：SSE 断开后不再信任墙上的旧帧（planNodeFrame 为空）",
  t.planNodeFrame() === null, "不信任旧帧");

// ★ 兼容性约定：schema 变了才是破坏性变更 ⇒ 当没有该节点，走兜底（不白屏）
t.setSseLive(true);
t.applySnapshot({ nodes: [{ node: t.PLAN_NODE, frame: {
  schema: "qyn.agent-plan/2", generated_at_ms: Date.now(), fresh_for_ms: 300000,
  level: "ok", plan: { slots: [] }, time: {} } }] });
check("推送：schema 不认识 ⇒ 整帧不用，走兜底（不白屏）",
  t.pushSigOf() === "poll" && agentHtml().includes("兜底专属·任务"), `sig=${t.pushSigOf()}`);

// ★ 帧没给 fresh_for_ms 时按默认 5 分钟判（契约里给了，但缺了不许当"永不过期"）
check("推送：帧缺 fresh_for_ms ⇒ 按默认 5 分钟判新鲜度",
  t.frameFresh({ generated_at_ms: Date.now() - 1000 }) === true &&
  t.frameFresh({ generated_at_ms: Date.now() - 400000 }) === false &&
  t.frameFresh({}) === false, "默认寿命");

// ★ 最后一道底线：推送和兜底**都没有**时也绝不白屏（显示"读取中…"，不是空块）
t.setSseLive(false);
t.setAgentFallback(null);
t.setTimeFallback(null);
t.syncPush();
t.renderAgent();
t.renderTime();
check("推送：推送与兜底都没有 ⇒ 显示「读取中…」，绝不白屏",
  agentHtml().includes("读取中") && timeHtml().includes("读取中"), "不白屏");
// 收尾：把观感还给后面的用例（它们只读 code / html，但别留一个空面板）
t.setAgent(AGENT_OK);
t.renderAgent();
t.setTime(TIME_OK);
t.renderTime();

check("CSS：收起时隐藏进度条", /\.panel\.collapsed\s+\.bar\s*\{[^}]*display:\s*none/.test(css), "bar");
check("CSS：收起时隐藏 PING（rate-ping）",
  /\.panel\.collapsed\s+\.rate-ping\s*\{[^}]*display:\s*none/.test(css), "rate-ping");
check("CSS：收起时 WAN 只留 LOSS（藏掉 PING）",
  /\.panel\.collapsed\s+\.body\.dual\s+\.wan-ping\s*\{[^}]*display:\s*none/.test(css), "WAN 只留一个");
check("CSS：展开时异常区限高并滚动",
  /\.tcard\.alert:not\(\.grow\)\s*\{[^}]*max-height:\s*84px/.test(css), "异常区滚动");
check("CSS：三个子框都能自己滚",
  /\.tcard\.next,\s*\.tcard\.due,\s*\.tcard\.alert\s*\{[^}]*overflow-y:\s*auto/.test(css), "可滚");
check("CSS：收起时时间列表纵向滚动",
  /\.panel\.collapsed\s+\.time-cards\s*\{[^}]*overflow-y:\s*auto/.test(css), "TIME 滚动");
check("CSS：宽度改成**权重**（面板不再写死 px，只留 min-width + grow 过渡）",
  /\.panel\s*\{[^}]*flex:\s*0 0 auto/.test(css) &&
  /\.panel\s*\{[^}]*min-width:\s*0/.test(css) &&
  /\.panel\s*\{[^}]*transition:\s*flex-grow/.test(css) &&
  !/\.panel\.collapsed\s*\{[^}]*flex:\s*0 0 186px/.test(css), "权重");
check("CSS：wide 与 collapsed 都不再自带固定宽度",
  /\.panel\.wide\s*\{[^}]*flex:\s*0 0 auto/.test(css) &&
  /\.panel\.wide\.collapsed\s*\{[^}]*flex:\s*0 0 auto/.test(css), "无固定宽");

// 灰色小字不许换行（用户 2026-10-14："灰色小字影响排版了"）
check("CSS：面板注解不许换行",
  /\.panel-note\s*\{[^}]*white-space:\s*nowrap/.test(css), "panel-note");
check("CSS：收起时隐藏面板注解",
  /\.panel\.collapsed\s+\.panel-note\s*\{[^}]*display:\s*none/.test(css), "collapsed note");
check("CSS：机器名不许换行（窄了截断）",
  /\.mname\s*\{[^}]*white-space:\s*nowrap/.test(css), "mname");
check("CSS：状态字不许换行",
  /\.state\s*\{[^}]*white-space:\s*nowrap/.test(css), "state");

// 收起态 TIME 的三条分工（用户 2026-10-14）
check("CSS：收起时课程名向左、其他信息靠右",
  /\.panel\.collapsed\s+\.tnext\s+\.tcourse\s*\{[^}]*align-self:\s*flex-start/.test(css) &&
  /\.panel\.collapsed\s+\.tnext\s+\.tmeta\s*\{[^}]*align-self:\s*flex-end/.test(css) &&
  /\.panel\.collapsed\s+\.tnext\s+\.tright\s*\{[^}]*align-items:\s*flex-end/.test(css),
  "左课程 / 右信息");
check("CSS：收起时 DUE 自己滚动",
  /\.panel\.collapsed\s+\.tcard\.due\s*\{[^}]*overflow-y:\s*auto/.test(css), "DUE 滚动");
check("CSS：收起时异常栏只留一行",
  /\.panel\.collapsed\s+\.tcard\.alert\s+\.tsub\s*\{[^}]*display:\s*none/.test(css), "异常栏压缩");

// 布局：页脚已删（那一条将来要做对话框与交互区）
check("布局：页脚已删", !/<footer/.test(html), "footer");
check("布局：底部预留条存在（现在是对话入口）",
  /<div id="dock"/.test(html), "dock");
check("CSS：预留条有固定高度（空着也不塌）—— 用户 2026-10-17 收到三分之二",
  /#dock\s*\{[^}]*flex:\s*0 0 43px/.test(css), "dock 高度");
check("布局：脚本不再去写已删掉的 #updated",
  !/getElementById\("updated"\)/.test(code), "残留引用");

// ---------------------------------------------------------------- 主题（可热插拔 UI）
const themeDir = path.join(__dirname, "web", "themes");
check("主题：注册表含 gilded 与 pixel-tui ✓",
  /THEMES\s*=/.test(code) && /"gilded"/.test(code) && /"tui"/.test(code), "主题注册表");
check("主题：<link id=theme-extra> 指向默认主题（简洁 ✓）",
  /id="theme-extra"[^>]*href="themes\/simple\.css"/.test(html), "主题链接");
check("主题：三套主题、简洁排第一（默认 ✓）",
  /THEMES\s*=\s*\[\s*\{\s*id:\s*"simple"/.test(code) &&
  /"gilded"/.test(code) && /"tui"/.test(code), "主题顺序");
check("主题：运行时切换（applyTheme + data-theme + 存 SETTINGS）✓",
  /function applyTheme/.test(code) && /data-theme/.test(code) && /SETTINGS\.theme\s*=/.test(code), "主题切换");
check("主题：快捷键 T 轮换 ✓",
  /e\.key === "t"/.test(code) && /cycleTheme\(\)/.test(code), "主题快捷键");
check("主题：?theme= 参数（无头截图用）✓",
  /theme=\(\[a-z0-9-\]\+\)/.test(code), "主题参数");
check("主题：设置面板里有主题分组 ✓",
  /themeSection\(\)/.test(code) && /data-theme-pick/.test(code), "主题设置项");
check("主题：themes/tui.css 存在且给了核心 token ✓",
  (() => {
    try {
      const c = fs.readFileSync(path.join(themeDir, "tui.css"), "utf8");
      return ["--bg", "--card", "--fg", "--dim", "--ok", "--warn", "--bad", "--accent", "--line", "--radius", "--font-latin"]
        .every(t => c.includes(t + ":"));
    } catch (e) { return false; }
  })(), "像素主题文件");
check("主题：pixel-tui.css **零外链**（墙上断网也能看）✓",
  (() => {
    try { return !/https?:\/\//.test(fs.readFileSync(path.join(themeDir, "tui.css"), "utf8")); }
    catch (e) { return false; }
  })(), "像素主题零外链");
check("主题：**不许改字号**（用户 2026-11-07 硬规则）—— **所有**主题 CSS 里都没有 font-size / letter-spacing ✓",
  (() => {
    try {
      return fs.readdirSync(themeDir)
        .filter(f => f.endsWith(".css"))
        .every(f => {
          const c = fs.readFileSync(path.join(themeDir, f), "utf8")
            .replace(/\/\*[\s\S]*?\*\//g, "");        // 去掉注释再查 ✓
          return !/font-size\s*:/.test(c) && !/letter-spacing\s*:/.test(c);
        });
    } catch (e) { return false; }
  })(), "主题不改字号");
check("主题：README 说明热插拔约定 ✓",
  (() => { try { return fs.readFileSync(path.join(themeDir, "README.md"), "utf8").includes("THEMES"); } catch (e) { return false; } })(),
  "主题文档");

// ---------------------------------------------------------------- Proxmox（SERVER 采集器，2026-11-08）
check("PROXMOX：SERVER 采集器只采三个状态（CPU/TEMP/RAM ✓）且契约与墙一致 ✓",
  (() => {
    try {
      const p = fs.readFileSync(path.join(__dirname, "..", "..", "scripts", "proxmox-collect.py"), "utf8");
      return /"node": node_id/.test(p) && /"cpu"/.test(p) && /"mem"/.test(p) &&
             /"disk"/.test(p) && /"net"/.test(p) && /"extra"/.test(p) && /pvesh/.test(p);
    } catch (e) { return false; }
  })(), "pve collector");

// ---------------------------------------------------------------- 结果
let failed = 0;
for (const c of checks) {
  if (!c.ok) failed++;
  console.log(`${c.ok ? "  OK  " : "  FAIL"}  ${c.name}${c.ok ? "" : "   <- " + c.detail}`);
}
console.log(failed === 0 ? `\n全部 ${checks.length} 项通过` : `\n${failed}/${checks.length} 项失败`);
process.exit(failed === 0 ? 0 : 1);
