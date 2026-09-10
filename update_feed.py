import csv
import html
import re
import urllib.request
from urllib.error import HTTPError, URLError
import time
import ssl
import concurrent.futures
import glob  # ★追加：フォルダ内のgoodsファイルを探すため

# (中略: COMMENT_TAG_PATTERN や clean_text はそのまま)

# ==========================================
# ★追加・変更：タイトルのフォーマットと特徴辞書設定
# ==========================================
ITEM_NO_PATTERN = re.compile(r"^(No\.|品番)\s*[A-Za-z0-9-]+\s*", re.IGNORECASE)
PROMO_SYMBOL_PATTERN = re.compile(r"[●◆■★].*$")

# 抽出したい機能キーワード（goods.csvのキーワードにも反応します）
FEATURE_KEYWORDS = ["幅広", "甲高", "歩きやすい", "日本製", "撥水", "防水", "軽量", "軽い", "洗える", "外反母趾", "3E"]
BRAND_NAMES = ["クロールバリエ", "COULEUR VARIE", "バスクラフト", "BATH CRAFT"]

# ★新規追加：goods_*.csv から特徴辞書を作成する関数
def load_feature_dict():
    feature_dict = {}
    # フォルダ内にある 'goods_' から始まるCSVファイルを探す
    goods_files = glob.glob("goods_*.csv")
    if not goods_files:
        print("※ goods_*.csv が見つかりません。タイトルからの特徴抽出のみ行います。")
        return feature_dict
    
    # 最新のファイルを使用
    latest_goods_file = sorted(goods_files)[-1]
    print(f"商品データ {latest_goods_file} を読み込み、特徴辞書を作成します...")
    
    try:
        # futureshopの出力はShift-JISが多いので cp932 を指定
        with open(latest_goods_file, "r", encoding="cp932", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_id = str(row.get("商品番号", "")).strip()
                keywords_str = row.get("キーワード", "")
                if item_id and keywords_str:
                    # カンマ区切りのキーワードをリスト化
                    keywords = [k.strip() for k in keywords_str.split(",")]
                    # FEATURE_KEYWORDS と一致するものだけ抽出
                    matched = [k for k in keywords if k in FEATURE_KEYWORDS]
                    if matched:
                        feature_dict[item_id] = list(dict.fromkeys(matched))
    except Exception as e:
        print(f"CSVの読み込みエラー: {e}")
        
    print(f"{len(feature_dict)}件の商品に特徴データを紐付けました。")
    return feature_dict

# スクリプト実行時に一度だけ特徴辞書を読み込む
ITEM_FEATURES = load_feature_dict()

# ★変更：商品ID(item_id)を受け取り、辞書データも結合するように修正
def format_title(original_title, item_id=""):
    title = original_title
    
    # 1. 不要な品番・プロモーション記号を削除
    title = ITEM_NO_PATTERN.sub("", title)
    title = PROMO_SYMBOL_PATTERN.sub("", title)
    
    # 2. 元のタイトル内の【】を抽出
    brackets = re.findall(r"【(.*?)】", title)
    title = re.sub(r"【.*?】", "", title)
    found_features = list(brackets)
    
    # 3. ★追加：goods.csvから取得した特徴（キーワード）があれば追加
    if item_id in ITEM_FEATURES:
        found_features.extend(ITEM_FEATURES[item_id])
    
    # 4. タイトル内に含まれている特徴を抽出し、タイトルから抜く
    for kw in FEATURE_KEYWORDS:
        if kw in title:
            if kw not in found_features:
                found_features.append(kw)
            title = title.replace(kw, "")
            
    # 5. ブランド名を抽出し、タイトルから抜く
    found_brand = ""
    for brand in BRAND_NAMES:
        if brand in title:
            found_brand = brand
            title = title.replace(brand, "")
            break
            
    title = MULTI_SPACE_PATTERN.sub(" ", title).strip()
    
    # 6. 並び替え結合
    final_title = ""
    if found_features:
        unique_features = list(dict.fromkeys(found_features))
        features_str = "・".join(unique_features)
        final_title += f"【{features_str}】 "
        
    final_title += title
    
    if found_brand:
        final_title += f" {found_brand}"
        
    return final_title.strip()

# (中略: 設定、check_image_exists はそのまま)

# ★変更：format_titleに item_id を渡すように修正
def process_row(row, timestamp):
    item_id = row.get("id", "")

    # 【0】説明文・タイトルのクリーニングと再構成
    if "description" in row:
        row["description"] = clean_text(row["description"])

    if "title" in row:
        cleaned_title = clean_text(row["title"])
        # format_title に item_id も渡して、辞書から特徴を引っ張れるようにする
        row["title"] = format_title(cleaned_title, item_id)

    # 【A】メイン画像の処理... (以降そのまま)

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
