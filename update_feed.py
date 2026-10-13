import csv
import html
import re
import urllib.request
from urllib.error import HTTPError, URLError
import time
import ssl
import concurrent.futures
import glob
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

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

def strip_html_to_text(html_text):
    """管理画面の商品説明文HTMLをプレーンテキストに変換"""
    if not html_text:
        return ""
    text = re.sub(r"<!--.*?-->", "", html_text, flags=re.DOTALL)  # コメントごと削除
    text = re.sub(r"<[^>]+>", " ", text)                          # タグを空白に
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ==========================================
# goods_*.csv（管理画面の商品データ一括ダウンロード）の読み込み
# ==========================================
# このスクリプトが goods CSV から使う列。管理画面「商品データ一括登録項目設定」で
# 以下がOFFにされると、説明文・タイトルが黙って劣化するため、欠けていたら停止する。
#   商品番号 / 商品名 / メイングループ … 固定項目（常に出力される）
#   商品説明文       … 「商品説明HTMLタグ」にチェック
#   キーワード       … 「キーワード」にチェック
GOODS_REQUIRED_COLUMNS = ["商品番号", "商品名", "メイングループ", "商品説明文", "キーワード"]
GOODS_MIN_ROWS = 100  # これより少なければダウンロード範囲の誤りとみなす

def load_goods():
    """goods_*.csv を1回だけ読み、説明文・特徴・商品名の辞書を返す"""
    desc_dict, feature_dict, title_dict, group_dict = {}, {}, {}, {}
    goods_files = glob.glob("goods_*.csv")
    if not goods_files:
        print("※ goods_*.csv が見つかりません。フィードの値のみで処理します。")
        return desc_dict, feature_dict, title_dict, group_dict

    latest_goods_file = sorted(goods_files)[-1]
    print(f"商品データ {latest_goods_file} を読み込みます...")

    with open(latest_goods_file, "r", encoding="cp932", errors="replace") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in GOODS_REQUIRED_COLUMNS if c not in header]
        if missing:
            raise SystemExit(
                f"\n[エラー] {latest_goods_file} に必要な列がありません: {missing}\n"
                "futureshop管理画面「商品データ一括登録項目設定」で該当項目にチェックを入れ、"
                "CSVをダウンロードし直してください。\n"
                f"必要な列: {GOODS_REQUIRED_COLUMNS}"
            )
        rows = list(reader)

    if len(rows) < GOODS_MIN_ROWS:
        raise SystemExit(
            f"\n[エラー] {latest_goods_file} の行数が {len(rows)} 件しかありません"
            f"（想定 {GOODS_MIN_ROWS} 件以上）。全商品を対象にダウンロードし直してください。"
        )

    for row in rows:
        item_id = str(row.get("商品番号", "")).strip()
        if not item_id:
            continue
        raw_html = row.get("商品説明文", "")
        if raw_html:
            desc_dict[item_id] = strip_html_to_text(raw_html)
        name = row.get("商品名", "").strip()
        if name:
            title_dict[item_id] = name
        group_dict[item_id] = row.get("メイングループ", "").strip()
        keywords_str = row.get("キーワード", "")
        if keywords_str:
            keywords = [k.strip() for k in keywords_str.split(",")]
            matched = [k for k in keywords if k in FEATURE_KEYWORDS]
            if matched:
                feature_dict[item_id] = list(dict.fromkeys(matched))

    print(f"{len(rows)}行を読み込みました（説明文 {len(desc_dict)}件 / 商品名 {len(title_dict)}件 / 特徴 {len(feature_dict)}件）")
    return desc_dict, feature_dict, title_dict, group_dict

# 起動時に一度だけ読み込み
ITEM_DESCRIPTIONS, ITEM_FEATURES, ITEM_TITLES, ITEM_GROUPS = load_goods()

# ==========================================
# 洗えないブランドのチェック
# ==========================================
# newmo / BATH CRAFT / BATH ROOM は洗えないため、「洗える」系の表現を載せない。
# ブランドは goods CSV のメイングループで判定（フィードのbrand列は全商品COULEUR VARIEのため使えない）。
# goods CSVに無い商品は商品名の文字列で判定する。
NON_WASHABLE_GROUP_PREFIXES = ("newmo", "BATH CRAFT", "BATH ROOM")
NON_WASHABLE_NAME_PATTERN = re.compile(r"newmo|ニューモ|BATH\s*CRAFT|バスクラフト|BATH\s*ROOM|バスルーム", re.IGNORECASE)
# 商品そのものが洗えると読める表現（「洗練」は対象外）
WASHABLE_CLAIM_PATTERN = re.compile(r"洗える|洗えて|丸洗い|洗濯機|洗濯可|洗濯OK|洗濯ＯＫ|水洗い|ウォッシャブル|washable", re.IGNORECASE)
# 問題ない文脈：否定表現、またはインソール・中敷だけ洗える場合
WASHABLE_OK_PATTERN = re.compile(r"(インソール|中敷)[^。]*$")  # 同じ文の中でインソール・中敷の話をしている
BODY_WASH_PATTERN = re.compile(r"丸洗い|洗濯機|ウォッシャブル|washable", re.IGNORECASE)  # 靴本体の話なのでインソール文脈でも警告
WASHABLE_NEGATION_PATTERN = re.compile(r"^[^。、]{0,12}(ません|ない|不可|NG)")
WASH_WARNINGS = []

def is_non_washable(item_id, title=""):
    group = ITEM_GROUPS.get(item_id, "")
    if group:
        return group.startswith(NON_WASHABLE_GROUP_PREFIXES)
    return bool(NON_WASHABLE_NAME_PATTERN.search(title or ""))

def find_washable_claims(text):
    """洗えると読める箇所の前後を返す（インソール限定・否定表現は除外）"""
    hits = []
    for m in WASHABLE_CLAIM_PATTERN.finditer(text or ""):
        before = text[max(0, m.start() - 80):m.start()]
        after = text[m.end():m.end() + 20]
        insole_only = WASHABLE_OK_PATTERN.search(before) and not BODY_WASH_PATTERN.search(m.group())
        if insole_only or WASHABLE_NEGATION_PATTERN.search(after):
            continue
        hits.append(text[max(0, m.start() - 15):m.end() + 15])
    return hits[:1]  # 同じ商品・同じ欄は1件に集約
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
BRAND_PAIR_PATTERN = re.compile(r"(COULEUR\s*VARIE|BATH\s*CRAFT)\s*[\(（]\s*(クロールバリエ|バスクラフト)\s*[\)）]", re.IGNORECASE)
EMPTY_BRACKET_PATTERN = re.compile(r"\(\s*\)|（\s*）|〈\s*〉|「\s*」|【\s*】|\[\s*\]")

# ==========================================
# 新作表記の自動切り替え ＆ 不要な定型文の削除
# ==========================================
# Y春夏新作 : 〜Y年8月はそのまま / Y年9月〜Y+1年3月は「Y新作」 / Y+1年4月〜は削除
# Y秋冬新作 : 〜Y+1年3月はそのまま / Y+1年4月〜は削除
# Y新作     : 〜Y+1年3月はそのまま / Y+1年4月〜は削除
# 日付は実行日（日本時間）で判定。毎年自動で適用される。
TODAY = datetime.now(ZoneInfo("Asia/Tokyo")).date()

SEASON_LABEL_PATTERN = re.compile(
    r"([0-9０-９]{4})\s*年?\s*(春夏|秋冬|SS|ＳＳ|S/S|AW|ＡＷ|A/W|FW)?\s*新作", re.IGNORECASE)
SPRING_SUMMER = ("春夏", "SS", "ＳＳ", "S/S")
_REMOVED = "\x00"
SEP = r"[・、,，/／]"

REMOVE_PHRASE_PATTERNS = [
    # ※予約商品と通常発送商品を一緒にご注文の場合は、予約商品が入荷後に同時発送となります
    re.compile(r"※?\s*予約商品と通常発送商品を一緒にご注文の場合は、?\s*予約商品が?\s*入荷後に?\s*同時発送となります。?"),
]

def _season_replace(m, today):
    year = int(unicodedata.normalize("NFKC", m.group(1)))
    season = (m.group(2) or "").upper()
    expire = (year + 1, 4)                     # Y+1年4月から削除
    now = (today.year, today.month)
    if now >= expire:
        return _REMOVED
    if season in SPRING_SUMMER and now >= (year, 9):
        return f"{year}新作"                   # Y年9月から「Y新作」
    return m.group(0)

def apply_label_rules(text, today=None):
    """新作表記の切り替えと、不要な定型文の削除（タイトル・説明文共通）"""
    if not text:
        return text
    for pat in REMOVE_PHRASE_PATTERNS:
        text = pat.sub("", text)
    today = today or TODAY
    text = SEASON_LABEL_PATTERN.sub(lambda m: _season_replace(m, today), text)
    if _REMOVED in text:
        # 削除した表記の後ろの「の」や区切り記号もいっしょに消す（例: 2024SS新作のサンダル → サンダル）
        text = re.sub(_REMOVED + r"の?\s*" + SEP + r"?\s*", "", text)
        text = text.replace(_REMOVED, "")
        # 【新色・】【・新色】のように残った区切り記号を掃除
        text = re.sub(SEP + r"\s*(?=[】\]）\)])", "", text)
        text = re.sub(r"(?<=[【\[（\(])\s*" + SEP, "", text)
        text = EMPTY_BRACKET_PATTERN.sub(" ", text)
        text = MULTI_SPACE_PATTERN.sub(" ", text).strip()
    return text

def format_title(original_title, item_id=""):
    title = original_title
    
    # ★追加：一番最初に「※返品・交換不可※」などのネガティブワードをすべて削除
    title = NEGATIVE_WORD_PATTERN.sub("", title)

    # 新作表記の切り替え・不要な定型文の削除
    title = apply_label_rules(title)
    
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
            
    # 「英字(カタカナ)」併記のブランド名はカタカナだけにする（末尾にカタカナのブランド名が付くため、英字は不要）
    title = BRAND_PAIR_PATTERN.sub(r" \2 ", title)

    # ブランド名を抽出して削除
    found_brand = ""
    for brand in BRAND_NAMES:
        if brand in title:
            found_brand = brand
            title = title.replace(brand, "")
            break

    # ブランド名を抜いた後に残る空のカッコを削除（例: COULEUR VARIE(クロールバリエ) → COULEUR VARIE()）
    title = EMPTY_BRACKET_PATTERN.sub(" ", title)

    title = MULTI_SPACE_PATTERN.sub(" ", title).strip()
    
    # 並び替え結合
    final_title = ""
    # 洗えないブランドは「洗える」系の特徴を付けない
    if is_non_washable(item_id, original_title):
        found_features = [f for f in found_features if not WASHABLE_CLAIM_PATTERN.search(f)]

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
    cleaned = apply_label_rules(cleaned)
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

    # 【D】洗えないブランドの最終チェック（説明文は自動で書き換えず、警告のみ）
    if is_non_washable(item_id, raw_title):
        for field in ("title", "description"):
            for snippet in find_washable_claims(row[field]):
                WASH_WARNINGS.append((item_id, ITEM_GROUPS.get(item_id, "?"), field, snippet))

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
    if WASH_WARNINGS:
        print(f"\n[要確認] 洗えないブランドの商品に「洗える」系の表現が {len(WASH_WARNINGS)} 件あります。futureshop管理画面で修正してください。")
        for item_id, group, field, snippet in WASH_WARNINGS:
            print(f"::warning title=洗えないブランド {item_id}::{group} / {field}: …{snippet}…")
    else:
        print("洗えないブランドのチェック：問題なし")

    print("全処理が完了しました！")

if __name__ == '__main__':
    main()
