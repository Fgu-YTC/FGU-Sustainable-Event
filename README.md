# FGU-Sustainable-Event

佛光大學「永續活動」列表。爬蟲自動從官網抓標題、活動日、時間（含多場次）、地點，寫入 `events.json` 後部署 GitHub Pages。**無後台、不手動改日期。**

- 預覽：https://fgu-ytc.github.io/FGU-Sustainable-Event/
- 嵌入：https://fgu-ytc.github.io/FGU-Sustainable-Event/?embed=1

## 自動更新

GitHub Actions 每天約台北 08:00 跑一次爬蟲並部署；也可在 Actions 手動觸發 **Scrape and Deploy Pages**。

本機：

```bash
pip install -r requirements.txt
python scraper.py
```
