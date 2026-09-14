# 永續活動列表（本機預覽 → GitHub Pages）

從 [佛光大學 SDGs 最新消息](https://sdgs.fgu.edu.tw/zh_tw/announcement/News) 自動抓取標有「永續活動」的公告，套用活動列表版型呈現。

## 本機

```bash
pip install -r requirements.txt
python scraper.py
python -m http.server 8080
```

開啟 http://localhost:8080

## GitHub Pages

1. 將本專案推到你的 GitHub 帳號（建議 repo 名稱：`SDGSACTIVITY`）
2. **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**
3. 到 **Actions** 手動跑一次 `Scrape and Deploy Pages`，或等每日排程

網站網址會是：

`https://<你的帳號>.github.io/SDGSACTIVITY/`
