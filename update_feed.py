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
# ==========================================
# ★追加：タイトルのフォーマット（並び替え）設定
# ==========================================
# 不要な品番（No.123など）や後半の装飾記号を消す正規表現
ITEM_NO_PATTERN = re.compile(r"^(No\.|品番)\s*[A-Za-z0-9-]+\s*", re.IGNORECASE)
PROMO_SYMBOL_PATTERN = re.compile(r"[●◆■★].*$")

# 抽出して先頭に持っていきたい機能・特徴キーワード
# ※必要に応じて追加・変更してください
FEATURE_KEYWORDS = ["幅広", "甲高", "歩きやすい", "日本製", "撥水", "軽量", "防水", "洗える"]

# 抽出して末尾に持っていきたいブランド名
# ※店舗で扱うブランド名を列挙してください
BRAND_NAMES = ["クロールバリエ", "COULEUR VARIE", "バスクラフト", "BATH CRAFT"]

def format_title(original_title):
    title = original_title
    
    # 1. 不要な品番・プロモーション記号を削除
    title = ITEM_NO_PATTERN.sub("", title)
    title = PROMO_SYMBOL_PATTERN.sub("", title)
    
    # 2. 元のタイトルに【定番人気商品】などの括弧があれば、中身を特徴として抽出＆削除
    brackets = re.findall(r"【(.*?)】", title)
    title = re.sub(r"【.*?】", "", title)
    
    # 3. 指定した特徴キーワードを抽出し、タイトル（商品名部分）から削除
    found_features = list(brackets)
    for kw in FEATURE_KEYWORDS:
        if kw in title:
            if kw not in found_features:
                found_features.append(kw)
            # タイトルからキーワードを抜く（例: "軽量パンプス" -> "パンプス"）
            title = title.replace(kw, "")
            
    # 4. ブランド名を抽出し、タイトル（商品名部分）から削除
    found_brand = ""
    for brand in BRAND_NAMES:
        if brand in title:
            found_brand = brand
            title = title.replace(brand, "")
            break # 1つ見つかればOK
            
    # 5. 商品名に残った余分な空白を綺麗にする（既存の MULTI_SPACE_PATTERN を利用）
    title = MULTI_SPACE_PATTERN.sub(" ", title).strip()
    
    # 6. 並び替え：【特徴】 商品名 ブランド名 の順に結合
    final_title = ""
    
    # 特徴があれば先頭に【特徴1・特徴2...】として付与
    if found_features:
        # 重複を排除しつつ順序を保持
        unique_features = list(dict.fromkeys(found_features))
        features_str = "・".join(unique_features)
        final_title += f"【{features_str}】 "
        
    # 商品名を追加
    final_title += title
    
    # ブランド名があれば末尾に付与
    if found_brand:
        final_title += f" {found_brand}"
        
    return final_title.strip()
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

    # 【0】説明文・タイトルのクリーニングと再構成
    if "description" in row:
        row["description"] = clean_text(row["description"])

    if "title" in row:
        # 既存のクリーニングをしてから、並び替えのフォーマットを適用
        cleaned_title = clean_text(row["title"])
        row["title"] = format_title(cleaned_title)

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
