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
BRAND_NAMES = ["クロールバリエ", "COULEUR VARIE", "バスクラフト", "BATH CRAFT", "newmo"]

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

ITEM_LIST_PRICES = {}  # 商品番号 → 定価（load_goods で埋める。ChatGPTフィードの price / sale_price 用）

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
        # ChatGPT用 sale_price：サイトに定価（取り消し線）を表示している商品だけ定価を控える
        list_price = str(row.get("定価", "")).strip()
        if list_price and str(row.get("定価表示方法", "")).strip() == "1":
            try:
                ITEM_LIST_PRICES[item_id] = int(float(list_price))
            except ValueError:
                pass
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
NEWMO_NAME_PATTERN = re.compile(r"newmo\s*[〈<（(]?\s*ニューモ\s*[〉>）)]?|ニューモ|newmo", re.IGNORECASE)
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

    # newmo は「newmo ニューモ」「newmoニューモ」「newmo〈ニューモ〉」「ニューモ」と表記がばらつくので
    # すべて「newmo」に統一（下のブランド抽出で末尾に回る）。「×ことりっぷ」のように残るコラボ表記は「ことりっぷコラボ」に
    if NEWMO_NAME_PATTERN.search(title):
        title = NEWMO_NAME_PATTERN.sub(" newmo ", title)
        title = re.sub(r"newmo\s*×\s*(\S+)", r"newmo \1コラボ", title)

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
# 3.5 ChatGPT広告フィード専用の整形
# ==========================================
# ChatGPTはタイトルを文章として読むため、Meta用の【特徴羅列】タイトルとは別に
# 「ブランド 商品名 特徴(最大3つ)」の自然な形に作り直す。Meta用フィードには影響しない。
# 列名は OpenAI Product Feed Spec に合わせる（https://developers.openai.com/commerce/product-feeds/spec）

# メイングループ → (brand列の値, タイトル先頭に付ける表記)
CG_BRANDS = [
    ("COULEURVARIE relaxing", "COULEUR VARIE", "クロールバリエ リラクシング"),
    ("COULEUR VARIE", "COULEUR VARIE", "クロールバリエ"),
    ("newmo", "newmo", "newmo"),
    ("BATH CRAFT", "BATH CRAFT", "バスクラフト"),
    ("BATH ROOM", "BATH ROOM", "バスルーム"),
    ("elevage", "elevage", "elevage"),
]
# goods CSVに無い商品はタイトルの文字列でブランドを判定
CG_BRAND_NAME_PATTERNS = [
    (re.compile(r"newmo|ニューモ", re.I), 1),
    (re.compile(r"BATH\s*CRAFT|バスクラフト", re.I), 2),
    (re.compile(r"BATH\s*ROOM|バスルーム", re.I), 3),
    (re.compile(r"elevage|エルバージュ", re.I), 4),
]
# タイトルから消すブランド表記（長いものから）
CG_BRAND_WORDS = re.compile(
    r"COULEUR\s*VARIE\s*[\(（]\s*クロールバリエ\s*[\)）]|COULEUR\s*VARIE\s*relaxing|COULEUR\s*VARIE|"
    r"クロールバリエ\s*リラクシング|クロールバリエ|クロール\s*バリエ|BATH\s*CRAFT|バスクラフト|BATH\s*ROOM|バスルーム|"
    r"newmo|ニューモ|elevage|エルバージュ", re.I)
CG_NOTE_PATTERN = re.compile(
    r"※?\s*(返品・?交換不可|交換・?返品不可|返品対応可|返品可|予約商品と通常発送商品を一緒にご注文の場合は[^※【]*?同時発送となります。?)\s*※?"
    r"|※[^※【\(（]{0,30}$")  # ※返品・交換不可※ / ※返品対応可 / ※予約商品と… / 末尾の※注記
CG_SALE_PATTERN = re.compile(r"[●/／]?\s*セール\s*●?|在庫販売")
CG_VARIANT_CODE = re.compile(r"\bM\d+\.")                                # BATH CRAFT の「M2.」などの色コード
CG_MENS_PATTERN = re.compile(r"[/／]?\s*(メンズ|紳士|男性)\s*[/／]?")
CG_PAREN_PATTERN = re.compile(r"[\(（]([^\(\)（）]*)[\)）]")
CG_PROMO_TOKENS = re.compile(r"新作|新色|定番人気|人気商品|セール|在庫販売|再入荷|予約|限定|シリーズ")
CG_BRACKET_PATTERN = re.compile(r"【([^】]*)】")

# 特徴の表記ゆれをそろえ、優先度の高い順に最大3つ載せる
CG_FEATURE_ALIASES = {"軽い": "軽量", "撥水": "はっ水", "幅調整": "幅調整可能"}
CG_FEATURE_ORDER = ["幅広3E", "幅広2E", "幅広", "3E", "2E", "甲高", "幅調整可能", "日本製", "防水", "はっ水", "軽量",
                    "歩きやすい", "痛くない", "柔らかい", "ムレない", "洗える"]
CG_FEATURE_SCAN = FEATURE_KEYWORDS + ["2E", "幅調整", "はっ水"]
CG_MAX_FEATURES = 3

# 商品名 → product_category（上から順に最初に当たったもの）
CG_CATEGORIES = [
    (r"トートバッグ|トート", "バッグ > トートバッグ"),   # 「リボンバレエ刺繍 帆布トートバッグ」が靴扱いにならないよう先に判定
    (r"ルームシューズ", "靴 > ルームシューズ"),
    (r"ブーツ|ブーティ", "靴 > ブーツ"),
    (r"サンダル|ミュール|サボ", "靴 > サンダル"),
    (r"スニーカー", "靴 > スニーカー"),
    (r"ローファー|モカシン|ドライビング", "靴 > ローファー・モカシン"),
    (r"バレエ|オペラ", "靴 > バレエシューズ"),
    (r"パンプス", "靴 > パンプス"),
    (r"スリッポン|シューズ|シューティ", "靴 > フラットシューズ"),
    (r"傘|umbrella|parasol", "ファッション小物 > 傘"),
    (r"リュック", "バッグ > リュック"),
    (r"サコッシュ|ポシェット|ショルダー", "バッグ > ショルダーバッグ"),
    (r"バッグ|バック(?!ル|ベルト)", "バッグ"),
]
# 靴以外（バッグ・傘）には付けない特徴
CG_SHOE_ONLY_FEATURES = {"幅広3E", "幅広2E", "幅広", "3E", "2E", "甲高", "幅調整可能", "歩きやすい", "痛くない", "ムレない"}
CG_CATEGORIES = [(re.compile(p, re.I), c) for p, c in CG_CATEGORIES]


def cg_brand(item_id, raw_title):
    group = ITEM_GROUPS.get(item_id, "")
    for prefix, brand, label in CG_BRANDS:
        if group.startswith(prefix):
            return brand, label
    for pat, idx in CG_BRAND_NAME_PATTERNS:
        if pat.search(raw_title or ""):
            return CG_BRANDS[idx][1], CG_BRANDS[idx][2]
    return CG_BRANDS[1][1], CG_BRANDS[1][2]


def cg_features(text):
    found = [kw for kw in CG_FEATURE_SCAN if kw in text]
    return found


def cg_normalize_features(features, non_washable, is_shoe=True):
    feats = []
    for f in features:
        f = CG_FEATURE_ALIASES.get(f.strip(), f.strip())
        if not f or f in feats:
            continue
        if non_washable and WASHABLE_CLAIM_PATTERN.search(f):
            continue
        feats.append(f)
    if "3E" in feats:
        feats = [x for x in feats if x not in ("幅広", "3E", "2E")] + ["幅広3E"]
    elif "幅広" in feats and "2E" in feats:
        feats = [x for x in feats if x not in ("幅広", "2E")] + ["幅広2E"]
    if not is_shoe:
        feats = [x for x in feats if x not in CG_SHOE_ONLY_FEATURES]
    known = [f for f in CG_FEATURE_ORDER if f in feats]
    return known[:CG_MAX_FEATURES]


def cg_title(item_id, raw_title):
    """ChatGPT用タイトル：ブランド 商品名 特徴・特徴・特徴"""
    brand, brand_label = cg_brand(item_id, raw_title)
    non_washable = is_non_washable(item_id, raw_title)
    t = unicodedata.normalize("NFKC", html.unescape(clean_title_text(raw_title)))

    t = CG_NOTE_PATTERN.sub(" ", t)
    t = NEGATIVE_WORD_PATTERN.sub(" ", t)
    t = apply_label_rules(t)

    features = list(ITEM_FEATURES.get(item_id, []))
    # 【】の中身：販促語は捨て、特徴はfeaturesへ、それ以外（フリルタイプ等）は商品名に残す
    keep_words = []
    for inner in CG_BRACKET_PATTERN.findall(t):
        for tok in re.split(r"[・,、/]", inner):
            tok = tok.strip()
            if not tok or CG_PROMO_TOKENS.search(tok):
                continue
            if tok in CG_FEATURE_SCAN or tok in CG_FEATURE_ALIASES:
                features.append(tok)
            else:
                keep_words.append(tok)
    t = CG_BRACKET_PATTERN.sub(" ", t)

    # （）の中身：検索キーワードの羅列や販促は捨てる（特徴だけ拾う）。短い補足（チュール等）は残す
    def _paren(m):
        inner = m.group(1).strip()
        features.extend(cg_features(inner))
        tokens = [x for x in re.split(r"[\s,、・/]+", inner) if x]
        if len(tokens) >= 3 or CG_PROMO_TOKENS.search(inner) or "店舗" in inner or not inner:
            return " "
        return f"({inner})"
    t = CG_PAREN_PATTERN.sub(_paren, t)
    # 閉じカッコの無いキーワード羅列（商品名が途中で切れている）も同様に捨てる
    m_open = re.search(r"[\(（]([^\)）]*)$", t)
    if m_open:
        features.extend(cg_features(m_open.group(1)))
        t = t[:m_open.start()]

    mens = bool(CG_MENS_PATTERN.search(t))
    t = CG_MENS_PATTERN.sub(" ", t)
    t = CG_SALE_PATTERN.sub(" ", t)
    t = CG_VARIANT_CODE.sub(" ", t)
    t = BRAND_PAIR_PATTERN.sub(" ", t)

    # 品番より前にキャッチコピーがある場合（「…が登場No.374282 …」）は品番より後ろを商品名とする
    m = ITEM_NO_PATTERN.search(t)
    if m and m.start() > 0 and len(t[m.end():].strip()) >= 6:
        t = t[m.end():]
    t = ITEM_NO_PATTERN.sub(" ", t)
    t = PROMO_SYMBOL_PATTERN.sub(" ", t)
    t = CG_BRAND_WORDS.sub(" ", t)
    t = re.sub(r"×\s*ことりっぷ", " ことりっぷコラボ ", t)
    t = re.sub(r"^[\s/／・,、]+|[\s/／・,、]+$", "", t)
    t = EMPTY_BRACKET_PATTERN.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()

    # 商品名に既に含まれる特徴は重ねない
    features.extend(cg_features(raw_title))
    is_shoe = cg_category(t).startswith("靴")
    feats = [f for f in cg_normalize_features(features, non_washable, is_shoe) if f not in t]

    # 認知度より商品の中身で選ばれるため「商品名 特徴 ブランド」の順（ブランドはbrand列でも伝わる）
    parts = []
    if mens:
        parts.append("メンズ")
    parts.append(t)
    parts.extend(keep_words)
    title = " ".join(p for p in parts if p)
    if feats:
        title += " " + "・".join(feats)
    title += f" {brand_label}"
    return truncate_field(re.sub(r"\s+", " ", title).strip(), 150), brand, mens


def cg_description(desc):
    """先頭の「No.品番」を外し、商品の説明から始まるようにする"""
    d = unicodedata.normalize("NFKC", desc or "")
    d = re.sub(r"^\s*" + ITEM_NO_PATTERN.pattern, "", d, flags=re.I)
    d = CG_NOTE_PATTERN.sub(" ", d) if d.startswith("※") else d
    return truncate_field(re.sub(r"\s+", " ", d).strip(), 5000)


def cg_category(title):
    for pat, cat in CG_CATEGORIES:
        if pat.search(title):
            return cat
    return ""


CHATGPT_FIELDNAMES = [
    "item_id", "title", "description", "url", "brand", "seller_name", "image_url", "additional_image_urls",
    "availability", "price", "sale_price", "condition", "product_category", "gender", "age_group", "gtin", "mpn",
]


# ==========================================
# ChatGPT用フィードから外す商品
# ==========================================
# ① 在庫切れ（futureshopフィードの availability が out of stock）… 毎日自動で判定。在庫が戻れば自動で復活
CHATGPT_EXCLUDE_OUT_OF_STOCK = True
# ② 傘（商品名に 傘 / umbrella / parasol）… CTRが低くCPCが高い、価格が低く採算が合わない（2026/10 広告実績より）
CHATGPT_EXCLUDE_TITLE_PATTERN = re.compile(r"傘|umbrella|parasol", re.IGNORECASE)
# ③' カテゴリごと外す（product_category の先頭一致）… バッグは訪問の約2割を占めるが注文がほぼ無い（2026/10 GA4より）
CHATGPT_EXCLUDE_CATEGORY_PREFIXES = ("バッグ",)
#    カテゴリ除外の例外として残す商品（在庫切れ・在庫極少の自動除外は通常どおりかかる）
CHATGPT_KEEP_IDS = {
    "4726y": "お財布ポシェット：ChatGPT広告で購入実績あり",
}
# ③ 個別に外す商品（商品番号: 理由）… 自動判定では拾えない事情で外したい商品があれば追加
#    例: "8515": "理由",   消せば翌朝から再掲載される
CHATGPT_EXCLUDE_IDS = {
}
# ④ 買える色・サイズがほとんど無い商品 … 毎朝、自社の商品ページ（bath-ec.com）の
#    バリエーション表を読み、「カートに入れる」が出ている組み合わせを数えて自動判定する。
#    以下のどちらかに当てはまれば除外（組み合わせが VARIANT_MIN_ROWS 未満の商品は対象外）
#      ・買える組み合わせが VARIANT_MAX_BUYABLE 以下（例: 35通り中1通り）
#      ・買える割合が VARIANT_MIN_RATIO 未満
#    商品ページが取れなかった商品は除外しない（判定不能＝掲載を続ける）。
CHATGPT_EXCLUDE_LOW_VARIANT_STOCK = True
VARIANT_MIN_ROWS = 3
VARIANT_MAX_BUYABLE = 1
VARIANT_MIN_RATIO = 0.15
VARIANT_FETCH_WORKERS = 5          # 自社サイトに負荷をかけないよう控えめに
VARIANT_TABLE_PATTERN = re.compile(
    r'<table[^>]*class="FS2_additional_image_tableVariation_table"[^>]*>(.*?)</table>', re.S)

# ⑤ 商品URLに付けるパラメータ（空＝付けない）
#    ChatGPT広告の流入は広告側で「utm_source=chatgpt / utm_medium=cpc」が自動で付き、GA4で計測できているため付けない。
#    ここで別の値を付けると広告側の値と重複・衝突し、GA4の集計が分かれてしまう。
CHATGPT_URL_PARAMS = ""

def add_url_params(url_str, params):
    if not url_str or not params or "utm_source=" in url_str:
        return url_str
    base, sep, frag = url_str.partition("#")
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{params}" + (sep + frag if sep else "")

def parse_variant_stock(page_html):
    """商品ページのHTMLから (組み合わせ数, 買える組み合わせ数) を返す。表が無ければ None"""
    m = VARIANT_TABLE_PATTERN.search(page_html or "")
    if not m:
        return None
    rows = [tr for tr in re.split(r"<tr\b", m.group(1))[1:] if "<td" in tr]
    buyable = sum(1 for tr in rows if 'name="varno_' in tr)
    return len(rows), buyable

def fetch_variant_stock(page_url):
    """商品ページを取得して在庫の組み合わせを数える。失敗したら None"""
    if not page_url:
        return None
    for attempt in range(2):
        try:
            req = urllib.request.Request(page_url, headers={"User-Agent": "Mozilla/5.0 (bath-feed-updater)"})
            with urllib.request.urlopen(req, timeout=20) as res:
                body = res.read().decode("cp932", errors="replace")
            return parse_variant_stock(body)
        except Exception:
            time.sleep(2)
    return None

def low_variant_reason(stock):
    if not stock:
        return ""
    rows, buyable = stock
    if rows < VARIANT_MIN_ROWS:
        return ""
    if buyable <= VARIANT_MAX_BUYABLE or buyable / rows < VARIANT_MIN_RATIO:
        return f"買える色・サイズが少ない（{rows}通り中{buyable}通り）"
    return ""

def chatgpt_exclude_reason(row):
    """ChatGPT用フィードから外す理由を返す（外さないなら空文字）"""
    item_id = str(row.get("id", "")).strip()
    url_code = str(row.get("link", "")).split("?")[0].rstrip("/").split("/")[-1]
    for key in (item_id, url_code):
        if key in CHATGPT_EXCLUDE_IDS:
            return "個別除外: " + CHATGPT_EXCLUDE_IDS[key]
    raw_title = ITEM_TITLES.get(item_id) or row.get("title", "")
    if CHATGPT_EXCLUDE_TITLE_PATTERN.search(raw_title or ""):
        return "傘"
    if item_id not in CHATGPT_KEEP_IDS and url_code not in CHATGPT_KEEP_IDS:
        category = cg_category(cg_title(item_id, raw_title)[0])
        if category.startswith(CHATGPT_EXCLUDE_CATEGORY_PREFIXES):
            return f"カテゴリ除外（{category}）"
    if CHATGPT_EXCLUDE_OUT_OF_STOCK and str(row.get("availability", "")).strip().lower().replace("_", " ") == "out of stock":
        return "在庫切れ"
    return ""


def build_chatgpt_row(row):
    """process_row 後の行（Meta用の列名）から ChatGPT用の行を作る"""
    item_id = row.get("id", "")
    raw_title = ITEM_TITLES.get(item_id) or row.get("title", "")
    title, brand, mens = cg_title(item_id, raw_title)

    # 価格：サイトで定価を見せている商品は price=定価 / sale_price=販売価格
    price = row.get("price", "")
    sale_price = ""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*([A-Z]{3})", price or "")
    if m:
        current, currency = float(m.group(1)), m.group(2)
        list_price = ITEM_LIST_PRICES.get(item_id)
        if list_price and list_price > current:
            price = f"{list_price} {currency}"
            sale_price = f"{int(current)} {currency}"

    out = {
        "item_id": item_id,
        "title": title,
        "description": cg_description(row.get("description", "")),
        "url": add_url_params(row.get("link", ""), CHATGPT_URL_PARAMS),
        "brand": brand,
        "seller_name": STORE_NAME,
        "image_url": row.get("image_link", ""),
        "additional_image_urls": row.get("additional_image_link", ""),
        "availability": str(row.get("availability", "")).strip().replace(" ", "_"),
        "price": price,
        "sale_price": sale_price,
        "condition": row.get("condition", "") or "new",
        "product_category": cg_category(title),
        "gender": "male" if mens else "female",
        "age_group": "adult",
        "gtin": row.get("gtin", ""),
        "mpn": row.get("mpn", ""),
    }

    # 洗えないブランドの最終チェック（ChatGPT用タイトルも対象）
    if is_non_washable(item_id, raw_title):
        for snippet in find_washable_claims(out["title"]):
            WASH_WARNINGS.append((item_id, ITEM_GROUPS.get(item_id, "?"), "chatgpt_title", snippet))
    return out

# ==========================================
# 3.6 Meta用フィードの仕上げ
# ==========================================
# タイトルの形（【特徴】 商品名 ブランド）は generate_pinterest.py が分解して使うため変えない。
# 直すのは ①brand ②sale_price ③タイトル本文のノイズ（※注記・キーワード羅列）だけ。
META_KEYWORD_PAREN = re.compile(r"[\(（]([^\(\)（）]*)[\)）]")

def clean_meta_title(title, has_sale_price):
    t = CG_NOTE_PATTERN.sub(" ", title)

    def _paren(m):
        inner = m.group(1).strip()
        tokens = [x for x in re.split(r"[\s,、・/]+", inner) if x]
        return " " if (len(tokens) >= 3 or not inner) else m.group(0)
    t = META_KEYWORD_PAREN.sub(_paren, t)
    # 閉じカッコの無いキーワード羅列（商品名が途中で切れている）は、末尾のブランド名だけ残して外す
    m_open = re.search(r"[\(（][^\)）]*$", t)
    if m_open:
        tail = t[m_open.start():].rstrip()
        brand_tail = next((b for b in BRAND_NAMES if tail.endswith(b)), "")
        t = t[:m_open.start()] + (" " + brand_tail if brand_tail else "")
    if has_sale_price:
        # 価格欄で割引が表示されるので、商品名本文の「/セール」「●セール●」は外す（【】内のタグは残す）
        head, sep, body = t.partition("】") if t.startswith("【") else ("", "", t)
        body = re.sub(r"[/／]\s*セール|●\s*セール\s*●|セール(?=\s|$)", " ", body)
        t = head + sep + body
    t = re.sub(r"\s+", " ", t).strip()
    return truncate_field(t, 150)


def build_meta_row(row):
    """process_row 後の行から Meta用の行を作る（ChatGPT用の行を作った後に呼ぶ）"""
    out = dict(row)
    item_id = row.get("id", "")
    raw_title = ITEM_TITLES.get(item_id) or row.get("title", "")
    out["brand"] = cg_brand(item_id, raw_title)[0]

    out["sale_price"] = ""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*([A-Z]{3})", row.get("price", "") or "")
    if m:
        current, currency = float(m.group(1)), m.group(2)
        list_price = ITEM_LIST_PRICES.get(item_id)
        if list_price and list_price > current:
            out["price"] = f"{list_price} {currency}"
            out["sale_price"] = f"{int(current)} {currency}"

    out["title"] = clean_meta_title(row.get("title", ""), bool(out["sale_price"]))
    return out

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
        
    # ChatGPT用の行を先に作る（Meta用の仕上げで price を書き換える前の値を使うため）
    chatgpt_rows, chatgpt_excluded, candidates = [], [], []
    for row in processed_rows:
        reason = chatgpt_exclude_reason(row)
        if reason:
            chatgpt_excluded.append((row.get("id", ""), reason))
        else:
            candidates.append(row)

    # 色・サイズごとの在庫を商品ページから読み取り、極端な商品を外す
    stocks = [None] * len(candidates)
    if CHATGPT_EXCLUDE_LOW_VARIANT_STOCK and candidates:
        print(f"商品ページから色・サイズ別の在庫を確認します（{len(candidates)}商品）...")
        urls = [str(r.get("link", "")).split("?")[0] for r in candidates]
        with concurrent.futures.ThreadPoolExecutor(max_workers=VARIANT_FETCH_WORKERS) as executor:
            stocks = list(executor.map(fetch_variant_stock, urls))
        ok = sum(1 for s_ in stocks if s_)
        print(f"  在庫表を読み取れた商品: {ok} / {len(candidates)}")
        if candidates and ok < len(candidates) * 0.5:
            print("::warning title=在庫の自動判定::商品ページの在庫表を半数以上読み取れませんでした（サイト構成の変更やアクセス制限の可能性）。在庫による除外は読み取れた商品だけに適用しています。")
    for row, stock in zip(candidates, stocks):
        reason = low_variant_reason(stock)
        if reason:
            chatgpt_excluded.append((row.get("id", ""), reason))
        else:
            chatgpt_rows.append(build_chatgpt_row(row))

    # Facebook用フィードの保存（brand / sale_price / タイトルのノイズを仕上げる）
    meta_rows = [build_meta_row(row) for row in processed_rows]
    meta_fieldnames = fieldnames + [f for f in ("brand", "sale_price") if f not in fieldnames]
    with open(output_file, "w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=meta_fieldnames)
        writer.writeheader()
        writer.writerows(meta_rows)

    print(f"Facebook用フィードを出力しました: {output_file}")

    # ChatGPT用フィードの保存

    with open(chatgpt_output_file, "w", encoding="utf-8", newline="") as outfile_chatgpt:
        writer_chatgpt = csv.DictWriter(outfile_chatgpt, fieldnames=CHATGPT_FIELDNAMES)
        writer_chatgpt.writeheader()
        writer_chatgpt.writerows(chatgpt_rows)
        
    print(f"ChatGPT用フィードを出力しました: {chatgpt_output_file}（{len(chatgpt_rows)}商品 / 除外 {len(chatgpt_excluded)}商品）")
    for item_id, reason in chatgpt_excluded:
        print(f"  - 除外 {item_id}: {reason}")
    if WASH_WARNINGS:
        print(f"\n[要確認] 洗えないブランドの商品に「洗える」系の表現が {len(WASH_WARNINGS)} 件あります。futureshop管理画面で修正してください。")
        for item_id, group, field, snippet in WASH_WARNINGS:
            print(f"::warning title=洗えないブランド {item_id}::{group} / {field}: …{snippet}…")
    else:
        print("洗えないブランドのチェック：問題なし")

    print("全処理が完了しました！")

if __name__ == '__main__':
    main()
