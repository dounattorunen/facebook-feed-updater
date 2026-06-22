import csv
import urllib.request
from urllib.error import HTTPError, URLError
import time
import ssl
import concurrent.futures # ★追加：並列処理用のモジュール

# --- 設定 ---
# futureshopのフィードURL
url = "https://ifeed.future-shop.net/sn/bath_d397e513c0bb34b415af1207cab70e4e58b03b2dbee4cdec30280e14be472338.csv"
output_file = "facebook_feed.csv"
# ----------

# Macローカルでのテスト用（SSL証明書エラー回避）
ssl._create_default_https_context = ssl._create_unverified_context

# 画像URLが実際に存在するか確認する関数
def check_image_exists(image_url):
    try:
        req = urllib.request.Request(image_url, method='HEAD')
        # ★タイムアウトを3秒に設定（サーバーの応答が遅い時にずっと待機するのを防ぐ）
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status == 200
    except Exception: # HTTPErrorやURLError、タイムアウトもまとめてFalseにする
        return False

# 1行（1商品）分の処理をまとめた関数
def process_row(row, timestamp):
    item_id = row.get("id", "")
    
    # 【A】メイン画像の処理
    original_url = row.get("image_link", "")
    if original_url:
        separator = "&" if "?" in original_url else "?"
        row["image_link"] = f"{original_url}{separator}v={timestamp}"
    
    # 【B】追加画像のルールベース自動生成
    additional_urls = []
    if item_id:
        dir_prefix = item_id[:3]
        for i in range(2, 7):
            img_num = f"{i:02d}"
            test_url = f"https://bath.fs-storage.jp/fs2cabinet/{dir_prefix}/{item_id}/{item_id}-m-{img_num}-pl.jpg"
            
            if check_image_exists(test_url):
                additional_urls.append(f"{test_url}?v={timestamp}")
            else:
                break 
    
    row["additional_image_link"] = ",".join(additional_urls)
    return row

def main():
    print("フィードのダウンロードと追加画像の自動探索を開始します...")
    print("（並列処理モード：高速化バージョン）")
    
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        content = response.read().decode('utf-8-sig')
        
    timestamp = int(time.time())
    reader = csv.DictReader(content.splitlines())
    fieldnames = list(reader.fieldnames)
    
    if "additional_image_link" not in fieldnames:
        fieldnames.append("additional_image_link")
        
    # イテレータをリスト化
    rows = list(reader)
    
    # ★ここからが並列処理（一気に20件ずつ処理する）
    processed_rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # map関数を使うことで、元のCSVの順番を保ったまま一気に処理できます
        processed_rows = list(executor.map(lambda r: process_row(r, timestamp), rows))
        
    # 結果をCSVに書き込み
    with open(output_file, "w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(processed_rows)
        
    print("完了しました！facebook_feed.csv を確認してください。")

if __name__ == "__main__":
    main()
