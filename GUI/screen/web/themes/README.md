# 屏幕主题（可热插拔 UI）

屏幕壳 `index.html` 里只放**基础样式**（布局 / 尺寸 / 结构 / 交互）。
所有"长得像什么"的东西——配色、字体、装饰、动效——都在 `themes/*.css` 里，
**运行时**可以切换，不用刷新页面。

## 现有主题

| 名称 | 文件 | 说明 |
|---|---|---|
| `simple`（默认） | `themes/simple.css` | 最开始那版：冷灰蓝底 + 亮蓝强调，扁平无装饰 |
| `gilded` | ——（内置在 `index.html` 的 `<style>` 里） | 黑金 / 鎏金深场 |
| `tui` | `themes/tui.css` | 终端 TUI（ANSI 配色、制表符边框、反色选中、方块进度条） |
| `acid` | `themes/acid.css` | 酸性新粗野（深灰 + 荧光酸绿 + 条码/半调网点 + 反白横幅） |
| `minecraft` | `themes/minecraft.css` | Minecraft 像素方块 GUI（浅灰面板 + 2px 明暗边 + 内凹槽位） |

## 怎么切换

1. **快捷键**：按 `T` 在主题间轮换（墙上 kiosk 直接按就行）
2. **设置面板**（右下角齿轮）：`外观 → 主题`
3. **URL 参数**（截图/调试用）：`?theme=pixel` 或 `?theme=gilded`
4. 选择会存进 `localStorage`（`qyn.settings.theme`），kiosk 重启后仍然生效

## 加一个新主题（三步）

1. 复制 `themes/pixel-tui.css` → `themes/你的主题.css`
2. 改三样东西：
   - `:root` 里的 token（**必须**提供 `--bg --panel --card --line --fg --dim --faint
     --ok --warn --bad --accent --lamp-off --lamp-dark --gold-700/500/300/100
     --hairline --hairline-soft --metal --radius --font-cn --font-latin`）
   - 装饰（边框/阴影/背景/扫描线…）
   - 动效（可全部关掉，像素主题就是这么干的）
3. 在 `index.html` 的 `THEMES` 表里加一行：`"你的主题": "themes/你的主题.css"`
   —— 到此为止，**不要改任何结构、类名或 JS 逻辑**。

## 主题必须遵守的约定

- **★ 不许改字号**（用户 2026-11-07 定的硬规则）：主题 CSS 里**禁止**出现 `font-size`
  与 `letter-spacing` ✗ —— 字号只由 `index.html` 的基础样式决定，
  这样切主题时版式**一格都不会跳** ✓（`render-test.js` 有断言盯着这条 ✓）。
- **只覆盖外观**：不改 `display/flex/grid` 布局（否则会挤爆格子）。
- **类名是契约**：`.panel .panel-title .panel-note .card .card-head .mname .k .v .state
  .extra .bar .offline .master .mk #dock #gear #settings #chat .msg` 等
  （`index.html` 的 `render-test.js` 里有 273 条断言在盯着它们）。
- **动效钩子**：整墙扫光用 `body.sweep`（JS 在展开时挂 5.2 秒）；
  单栏装饰可用 `.panel.sweep`。主题可以选择**忽略**这些钩子（像素主题就全部关掉了）。
- **无障碍**：`@media (prefers-reduced-motion: reduce)` 里要能把动画关掉。
- **零外链**：主题文件里**不许**出现 `http://` / `https://` 的字体、图片、CDN
  （墙上断网也要能看）。要图片就内联 base64 或纯 CSS 画。

## 校验

```bash
node CENTRAL/GUI/screen/render-test.js      # 273 项，含主题注册与切换的断言
```
