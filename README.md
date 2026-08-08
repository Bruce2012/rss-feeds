# 网页转 RSS

一个把“没有 RSS 的网站列表页”转成标准 RSS 2.0 文件的小工具。适合网站结构稳定的页面；如果页面内容由 JavaScript 动态渲染，需要换用 Playwright 等无头浏览器方案。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python make_rss.py --config feeds.example.yaml --output-dir out
```

生成的 `out/*.xml` 可以直接添加到 Feedly、NetNewsWire、Folo 等 RSS 阅读器，也可以托管到 GitHub Pages / Vercel 后用阅读器订阅。

## 配置说明

复制示例配置并修改：

```bash
cp feeds.example.yaml feeds.yaml
```

每个 feed 最少需要 4 个字段：

| 字段 | 说明 |
| --- | --- |
| `url` | 要抓取的网页地址 |
| `item_selector` | 圈出列表中每一项的 CSS 选择器 |
| `title_selector` | 每一项标题所在元素 |
| `link_selector` | 标题链接所在元素 |

可选字段：`date_selector`（时间元素）、`date_formats`（strptime 时间格式）、`content_selector`（摘要/正文）、`max_items`（最多输出条数，默认 50）、`feed_url`（对外公开的 XML 地址，用于 `atom:link` 自引用）、`user_agent`、`timeout`。

选择器获取方式：浏览器打开目标页面，在列表项标题上右键 -> 检查，在 Elements 面板右键该元素 -> Copy -> Copy selector，再复制到配置里。

## 定时更新

脚本每次运行都会重新抓取并覆盖输出文件，适合放进定时任务。

macOS（launchd）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.local.rss-fetch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/RSS订阅/.venv/bin/python</string>
    <string>/path/to/RSS订阅/make_rss.py</string>
    <string>--config</string>
    <string>/path/to/RSS订阅/feeds.yaml</string>
    <string>--output-dir</string>
    <string>/path/to/RSS订阅/out</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
</dict>
</plist>
```

保存为 `~/Library/LaunchAgents/com.local.rss-fetch.plist` 后执行：

```bash
launchctl load ~/Library/LaunchAgents/com.local.rss-fetch.plist
```

Linux / macOS 也可以直接用 cron，每小时运行一次：

```cron
0 * * * * cd /path/to/RSS订阅 && .venv/bin/python make_rss.py --config feeds.yaml --output-dir out
```

## GitHub Actions 稳定订阅

把项目推到 GitHub 后，仓库里的 `.github/workflows/rss.yml` 会每小时自动运行一次：

1. 抓取 `feeds.yaml` 里配置的网页。
2. 生成 `public/*.xml`。
3. 自动发布到 `gh-pages` 分支，也就是 GitHub Pages 站点。

首次部署步骤：

```bash
# 在 GitHub 新建仓库后，在本目录执行
git remote add origin https://github.com/<用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

然后在 GitHub 仓库的 `Settings -> Pages -> Build and deployment` 中，把 Source 设为 `Deploy from a branch`，分支选择 `gh-pages`，目录选 `/ (root)`。接着在 `Actions` 页面手动运行一次 `Build RSS`，一两分钟后 RSS 地址就是：

```text
https://<用户名>.github.io/<仓库名>/sspai.xml
```

以后每小时都会自动更新，不需要电脑保持开机。之后要订阅其他网站，只需修改 `feeds.yaml` 并推送到 `main` 分支即可。

注意：公开仓库的 Actions 免费且无限；私有仓库每月有 2000 分钟的免费额度，按每小时跑一次计算够用。
