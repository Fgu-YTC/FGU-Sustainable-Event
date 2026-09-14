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

## 嵌進佛光 SDGs 官網

目標頁：https://sdgs.fgu.edu.tw/zh_tw/announcement/test

1. 登入官網後台，編輯該頁內容  
2. 把頁面標題 `test` 改成 `永續活動`（可選）  
3. 用「原始碼／HTML」模式，把原本的 `123` 換成 `orbit-paste-snippet.html` 裡的內容  
4. 儲存後重新整理頁面  

嵌入後會載入 GitHub Pages，資料仍由 Actions 自動更新。
