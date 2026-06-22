import csv
import urllib.request
from urllib.error import HTTPError, URLError
import time

# --- 設定 ---
# futureshopのフィードURL
url = "https://ifeed.future-shop.net/sn/bath_d397e513c0bb34b415af1207cab70e4e58b03b2dbee4cdec30280e14be472338.csv"
output_file = "facebook_feed.csv"
# ----------

# 画像URLが実際に存在するか確認する関数（高速なHEADリクエストを使用）
def check_image_exists(image_url):
    try:
        # 画像本体をダウンロードせず、ヘッダー情報だけを取得して存在確認する
        req = urllib.request.Request(image_url, method='HEAD')
        with urllib.request.urlopen(req) as response:
            return response.status == 200
    except (HTTPError, URLError):
        return False

print("フィードのダウンロードと追加画像の自動探索を開始します...")
print("（※画像の存在チェックを行うため、完了まで1〜2分程度かかります）")

# 1. URLからフィードデータを取得
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    content = response.read().decode('utf-8-sig')

# 2. ユニークパラメータ（現在日時のタイムスタンプ）を生成
timestamp = int(time.time())

# 3. データを加工して新しいCSVとして保存
reader = csv.DictReader(content.splitlines())
fieldnames = list(reader.fieldnames)

# ヘッダーに 'additional_image_link' が無ければ追加する
if "additional_image_link" not in fieldnames:
    fieldnames.append("additional_image_link")

with open(output_file, "w", encoding="utf-8", newline="") as outfile:
    writer = csv.DictWriter(outfile, fieldnames=fieldnames)
    writer.writeheader()
    
    for row in reader:
        item_id = row.get("id", "")
        
        # 【A】メイン画像の処理
        original_url = row.get("image_link", "")
        if original_url:
            separator = "&" if "?" in original_url else "?"
            row["image_link"] = f"{original_url}{separator}v={timestamp}"
        
        # 【B】追加画像のルールベース自動生成
        additional_urls = []
        if item_id:
            # フォルダ名用：IDの最初の3文字を取得 (例: "644230" -> "644", "10037" -> "100")
            dir_prefix = item_id[:3]
            
            # 画像「02」から最大「06」までチェックする（上限は必要に応じて変更可能）
            for i in range(2, 7):
                img_num = f"{i:02d}" # 数字を 02, 03, 04 の形式にする
                
                # ルールに基づいた画像URLを組み立て
                test_url = f"https://bath.fs-storage.jp/fs2cabinet/{dir_prefix}/{item_id}/{item_id}-m-{img_num}-pl.jpg"
                
                # 画像が存在するかチェック
                if check_image_exists(test_url):
                    # 存在すればパラメータを付けてリストに追加
                    additional_urls.append(f"{test_url}?v={timestamp}")
                else:
                    # 存在しない（連番が途切れた）場合は、それ以上のチェックをやめて次の商品へ
                    break 
        
        # リストをカンマ区切りの文字列にしてセット
        row["additional_image_link"] = ",".join(additional_urls)
        
        # 1行書き込み
        writer.writerow(row)

print("完了しました！facebook_feed.csv を確認してください。")
