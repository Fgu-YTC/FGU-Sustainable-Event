# FGU-Sustainable-Event

佛光大學 SDGs「永續活動」列表：自動爬蟲 + GitHub Pages 後台管理日期。

## 本機

```bash
pip install -r requirements.txt
python scraper.py
python -m http.server 8080
```

開啟 http://localhost:8080

## GitHub Pages

- 後台（可改日期、重新撈取）：https://fgu-ytc.github.io/FGU-Sustainable-Event/
- 嵌入用（佛光官網，不可改）：`...?embed=1`

## 嵌進佛光 SDGs 官網

1. 日期與重新撈取在後台操作  
2. 官網只貼 `orbit-paste-snippet.html`  
3. 頁面標題建議改成「永續活動」
