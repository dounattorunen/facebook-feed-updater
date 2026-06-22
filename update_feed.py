import csv
import urllib.request
import time

# futureshopのフィードURL
url = "https://ifeed.future-shop.net/sn/bath_d397e513c0bb34b415af1207cab70e4e58b03b2dbee4cdec30280e14be472338.csv"
output_file = "facebook_feed.csv"

# 1. URLからフィードデータを取得（文字化け防止のため utf-8-sig でデコード）
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    content = response.read().decode('utf-8-sig')

# 2. ユニークパラメータ（現在日時のタイムスタンプ）を生成
timestamp = int(time.time())

# 3. データを加工して新しいCSVとして保存
with open(output_file, "w", encoding="utf-8", newline="") as outfile:
    reader = csv.DictReader(content.splitlines())
    # フィードの項目名（ヘッダー）をそのまま引き継ぐ
    writer = csv.DictWriter(outfile, fieldnames=reader.fieldnames)
    writer.writeheader()
    
    for row in reader:
        original_url = row.get("image_link", "")
        # image_link列が存在し、値が空でなければパラメータを付与
        if original_url:
            separator = "&" if "?" in original_url else "?"
            row["image_link"] = f"{original_url}{separator}v={timestamp}"
        
        writer.writerow(row)