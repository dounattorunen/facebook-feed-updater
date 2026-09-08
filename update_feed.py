import csv
import html
import re
import urllib.request
from urllib.error import HTTPError, URLError
import time
import ssl
import concurrent.futures

# 商品説明文に残っている <クラリーノ> のような山括弧タグ（注釈タグ）を除去する
COMMENT_TAG_PATTERN = re.compile(r"<[^<>]*>")
# 半角スペース・全角スペースが2つ以上連続している箇所をまとめる
MULTI_SPACE_PATTERN = re.compile(r"[ 　]{2,}")

def clean_text(text):
    if not text:
        return text
    # 対応する < が無い壊れたコメント断片（-->  や <!-- 単体）を除去
    text = text.replace("<!--", "").replace("-->", "")
    # ペアになっている山括弧タグ（例：<クラリーノ>）を除去
    text = COMMENT_TAG_PATTERN.sub("", text)
    # 上記で拾いきれない孤立した < や > も念のため除去
    text = text.replace("<", "").replace(">", "")
    # &nbsp; や &#10003; などのHTML実体参照を実際の文字に変換
    text = html.unescape(text)
    # デコードで生まれる非改行スペース(\xa0)を通常の半角スペースに統一
    text = text.replace("\xa0", " ")
    # 連続する空白（全角含む）を1つの半角スペースにまとめる
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    return text.strip()

# --- 設定 ---
# futureshopのフィードURL
url = "https://ifeed.future-shop.net/sn/bath_d397e513c0bb34b415af1207cab70e4e58b03b2dbee4cdec30280e14be472338.csv"
output_file = "facebook_feed.csv"
chatgpt_output_file = "chatgpt_feed.csv" # ★追加：ChatGPT用の出力ファイル名
STORE_NAME = "BATH ONLINE SHOP"          # ★追加：ChatGPTで必須となる店舗名(適宜変更してください)
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

    # 【0】説明文・タイトルのクリーニング（タグ除去・実体参照デコード・空白圧縮）
    for field in ("description", "title"):
        if field in row:
            row[field] = clean_text(row[field])

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
        
    # ==========================================
    # Facebook用フィードの保存 (元の処理そのまま)
    # ==========================================
    with open(output_file, "w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(processed_rows)
        
    print(f"Facebook用フィードを出力しました: {output_file}")
    
    # ==========================================
    # ChatGPT用フィードの保存 (新規追加)
    # ==========================================
    # 1. カラム名の変換・追加
    chatgpt_fieldnames = []
    for f in fieldnames:
        if f == "id": chatgpt_fieldnames.append("item_id")
        elif f == "link": chatgpt_fieldnames.append("url")
        elif f == "image_link": chatgpt_fieldnames.append("image_url")
        else: chatgpt_fieldnames.append(f)
        
    if "seller_name" not in chatgpt_fieldnames:
        chatgpt_fieldnames.append("seller_name")
        
    # 2. データの中身の変換
    chatgpt_rows = []
    for row in processed_rows:
        new_row = {}
        for k, v in row.items():
            if k == "id": 
                new_row["item_id"] = v
            elif k == "link": 
                new_row["url"] = v
            elif k == "image_link": 
                new_row["image_url"] = v
            elif k == "availability":
                # OpenAIの仕様に合わせてアンダースコアに置換 (in stock -> in_stock)
                new_row["availability"] = str(v).replace(" ", "_")
            else:
                new_row[k] = v
                
        # 必須項目の店舗名を追加
        new_row["seller_name"] = STORE_NAME
        chatgpt_rows.append(new_row)
        
    # 3. CSVファイルとして保存
    with open(chatgpt_output_file, "w", encoding="utf-8", newline="") as outfile_chatgpt:
        writer_chatgpt = csv.DictWriter(outfile_chatgpt, fieldnames=chatgpt_fieldnames)
        writer_chatgpt.writeheader()
        writer_chatgpt.writerows(chatgpt_rows)
        
    print(f"ChatGPT用フィードを出力しました: {chatgpt_output_file}")
    print("全処理が完了しました！")

if __name__ == '__main__':
    main()
