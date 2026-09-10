import csv
import html
import re
import urllib.request
from urllib.error import HTTPError, URLError
import time
import ssl
import concurrent.futures
import glob

# ==========================================
# 1. 基本設定
# ==========================================
# futureshopのフィードURL
url = "https://ifeed.future-shop.net/sn/bath_d397e513c0bb34b415af1207cab70e4e58b03b2dbee4cdec30280e14be472338.csv"
output_file = "facebook_feed.csv"
chatgpt_output_file = "chatgpt_feed.csv" 
STORE_NAME = "BATH ONLINE SHOP"          

# Macローカルでのテスト用（SSL証明書エラー回避）
ssl._create_default_https_context = ssl._create_unverified_context

# ==========================================
# 2. テキスト処理・フォーマット設定
# ==========================================
COMMENT_TAG_PATTERN = re.compile(r"<[^<>]*>")
MULTI_SPACE_PATTERN = re.compile(r"[  ]{2,}")
ITEM_NO_PATTERN = re.compile(r"^(No\.|品番)\s*[A-Za-z0-9-]+\s*", re.IGNORECASE)
# ★追加：説明文の途中にあっても見つけ出せるパターン（先頭縛りの ^ を外したもの）
DESC_NO_PATTERN = re.compile(r"(No[\.,、]|品番)\s*[A-Za-z0-9\-]+\s*", re.IGNORECASE)
PROMO_SYMBOL_PATTERN = re.compile(r"[●◆■★].*$")
# ★追加：返品交換不可などのネガティブワードをまとめて削除する正規表現
NEGATIVE_WORD_PATTERN = re.compile(r"※?(返品・交換不可|交換返品不可|返品交換不可)※?")

# 抽出したい機能キーワード
FEATURE_KEYWORDS = ["幅広", "甲高", "歩きやすい", "日本製", "撥水", "防水", "軽量", "軽い", "洗える", "3E", "柔らかい", "痛くない", "ムレない"]
BRAND_NAMES = ["クロールバリエ", "COULEUR VARIE", "バスクラフト", "BATH CRAFT"]

def clean_text(text):
    if not text:
        return text
    text = text.replace("<!--", "").replace("-->", "")
    text = COMMENT_TAG_PATTERN.sub("", text)
    text = text.replace("<", "").replace(">", "")
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    text = text.strip()
    # ★変更：説明文の最初の方にある「カラーバリエーション」や色名を飛ばして、
    # 「No.」や「品番」から文章が始まるようにカットする
    match = DESC_NO_PATTERN.search(text)
    # 最初から500文字以内に「No.」が見つかった場合、それより前をすべて消す
    if match and match.start() < 500:
        text = text[match.start():]
        
    return text.strip()

def load_feature_dict():
    feature_dict = {}
    goods_files = glob.glob("goods_*.csv")
    if not goods_files:
        print("※ goods_*.csv が見つかりません。タイトルからの特徴抽出のみ行います。")
        return feature_dict
    
    latest_goods_file = sorted(goods_files)[-1]
    print(f"商品データ {latest_goods_file} を読み込み、特徴辞書を作成します...")
    
    try:
        with open(latest_goods_file, "r", encoding="cp932", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_id = str(row.get("商品番号", "")).strip()
                keywords_str = row.get("キーワード", "")
                if item_id and keywords_str:
                    keywords = [k.strip() for k in keywords_str.split(",")]
                    matched = [k for k in keywords if k in FEATURE_KEYWORDS]
                    if matched:
                        feature_dict[item_id] = list(dict.fromkeys(matched))
    except Exception as e:
        print(f"CSVの読み込みエラー: {e}")
        
    print(f"{len(feature_dict)}件の商品に特徴データを紐付けました。")
    return feature_dict
def strip_html_to_text(html_text):
    """管理画面の商品説明文HTMLをプレーンテキストに変換"""
    if not html_text:
        return ""
    text = re.sub(r"<!--.*?-->", "", html_text, flags=re.DOTALL)  # コメントごと削除
    text = re.sub(r"<[^>]+>", " ", text)                          # タグを空白に
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def load_description_dict():
    """goods_*.csv の「商品説明文」を商品番号ベースで辞書化"""
    desc_dict = {}
    goods_files = glob.glob("goods_*.csv")
    if not goods_files:
        return desc_dict
    latest_goods_file = sorted(goods_files)[-1]
    try:
        with open(latest_goods_file, "r", encoding="cp932", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_id = str(row.get("商品番号", "")).strip()
                raw_html = row.get("商品説明文", "")
                if item_id and raw_html:
                    desc_dict[item_id] = strip_html_to_text(raw_html)
    except Exception as e:
        print(f"商品説明文の読み込みエラー: {e}")
    print(f"{len(desc_dict)}件の商品に管理画面の説明文を紐付けました。")
    return desc_dict

ITEM_DESCRIPTIONS = load_description_dict()
# 起動時に一度だけ特徴辞書を作成
ITEM_FEATURES = load_feature_dict()
ITEM_TITLES = load_title_dict()
# titleのクリーニングはHTML除去のみ。description用のNo.カット処理は使わない
def clean_title_text(text):
    if not text:
        return text
    text = text.replace("<!--", "").replace("-->", "")
    text = COMMENT_TAG_PATTERN.sub("", text)
    text = text.replace("<", "").replace(">", "")
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = MULTI_SPACE_PATTERN.sub(" ", text)
    return text.strip()

# 位置を問わず「No./NO,/№/品番」＋番号（スラッシュ区切りの複数品番も含む）を除去
ITEM_NO_PATTERN = re.compile(
    r"(№|No[\.,、]?|品番)\s*[0-9A-Za-z\-]+(?:\s*[/／]\s*[0-9A-Za-z\-]+)*\s*",
    re.IGNORECASE
)
def format_title(original_title, item_id=""):
    title = original_title
    
    # ★追加：一番最初に「※返品・交換不可※」などのネガティブワードをすべて削除
    title = NEGATIVE_WORD_PATTERN.sub("", title)
    
    # 不要な記号を削除
    title = ITEM_NO_PATTERN.sub("", title)
    title = PROMO_SYMBOL_PATTERN.sub("", title)
    
    # 既存の【】を抽出
    brackets = re.findall(r"【(.*?)】", title)
    title = re.sub(r"【.*?】", "", title)
    found_features = list(brackets)
    
    # goods.csvの特徴を追加
    if item_id in ITEM_FEATURES:
        found_features.extend(ITEM_FEATURES[item_id])
    
    # タイトル内の特徴を抽出して削除
    for kw in FEATURE_KEYWORDS:
        if kw in title:
            if kw not in found_features:
                found_features.append(kw)
            title = title.replace(kw, "")
            
    # ブランド名を抽出して削除
    found_brand = ""
    for brand in BRAND_NAMES:
        if brand in title:
            found_brand = brand
            title = title.replace(brand, "")
            break
            
    title = MULTI_SPACE_PATTERN.sub(" ", title).strip()
    
    # 並び替え結合
    final_title = ""
    if found_features:
        unique_features = list(dict.fromkeys(found_features))
        features_str = "・".join(unique_features)
        final_title += f"【{features_str}】 "
        
    final_title += title
    
    if found_brand:
        final_title += f" {found_brand}"
        
    return final_title.strip()

# ==========================================
# 3. 画像確認・行処理
# ==========================================
def check_image_exists(image_url):
    try:
        req = urllib.request.Request(image_url, method='HEAD')
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status == 200
    except Exception:
        return False

def truncate_field(text, max_len):
    if text and len(text) > max_len:
        return text[:max_len].rstrip()
    return text

def process_row(row, timestamp):
    item_id = row.get("id", "")

    # 【0-1】説明文：goods CSV（管理画面の生データ）を優先し、無ければ従来通りフィードの値を使う
    if item_id in ITEM_DESCRIPTIONS:
        raw_desc = ITEM_DESCRIPTIONS[item_id]
    else:
        raw_desc = row.get("description", "")

    cleaned = clean_text(raw_desc)
    cleaned = re.sub(r"^カラーバリエーション\s*", "", cleaned)
    row["description"] = cleaned

    # 【0-2】タイトル：goods CSVの商品名を優先。専用の軽いクリーニングを使う（clean_textは使わない）
    if item_id in ITEM_TITLES:
        raw_title = ITEM_TITLES[item_id]
    else:
        raw_title = row.get("title", "")

    cleaned_title = clean_title_text(raw_title)
    row["title"] = format_title(cleaned_title, item_id)

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

    # 【C】文字数の安全装置（Facebook/ChatGPT両フィードとも上限5000/150文字）
    row["description"] = truncate_field(row["description"], 5000)
    row["title"] = truncate_field(row["title"], 150)

    return row
# ==========================================
# 4. メイン処理
# ==========================================
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
        
    rows = list(reader)
    
    processed_rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        processed_rows = list(executor.map(lambda r: process_row(r, timestamp), rows))
        
    # Facebook用フィードの保存
    with open(output_file, "w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(processed_rows)
        
    print(f"Facebook用フィードを出力しました: {output_file}")
    
    # ChatGPT用フィードの保存
    chatgpt_fieldnames = []
    for f in fieldnames:
        if f == "id": chatgpt_fieldnames.append("item_id")
        elif f == "link": chatgpt_fieldnames.append("url")
        elif f == "image_link": chatgpt_fieldnames.append("image_url")
        else: chatgpt_fieldnames.append(f)
        
    if "seller_name" not in chatgpt_fieldnames:
        chatgpt_fieldnames.append("seller_name")
        
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
                new_row["availability"] = str(v).replace(" ", "_")
            else:
                new_row[k] = v
                
        new_row["seller_name"] = STORE_NAME
        chatgpt_rows.append(new_row)
        
    with open(chatgpt_output_file, "w", encoding="utf-8", newline="") as outfile_chatgpt:
        writer_chatgpt = csv.DictWriter(outfile_chatgpt, fieldnames=chatgpt_fieldnames)
        writer_chatgpt.writeheader()
        writer_chatgpt.writerows(chatgpt_rows)
        
    print(f"ChatGPT用フィードを出力しました: {chatgpt_output_file}")
    print("全処理が完了しました！")

if __name__ == '__main__':
    main()
