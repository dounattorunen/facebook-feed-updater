import csv
import html
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# ==========================================
# 設定
# ==========================================
INPUT_FILE = "facebook_feed.csv"
OUTPUT_DIR = "."                         # カテゴリ別フィードの出力先（pinterest_feed.xmlと同じ場所）
ALL_FEED_FILE = "pinterest_feed.xml"     # 従来の全件フィード（互換用）
STATE_FILE = "pinterest_seen.json"       # 初めてフィードに載った日を記録（要コミット）

SHOP_TITLE = "BATH ONLINE SHOP"
SHOP_LINK = "https://www.bath-ec.com"

NEW_ONLY_DAYS = 14        # 初登場からこの日数以内の商品だけをフィードに載せる
FALLBACK_ITEMS = 1        # 新作が無いカテゴリにも最低この件数を載せる（空フィードはPinterestがエラーにするため）
TITLE_MAX = 100           # Pinterestのタイトル上限
DESC_MAX = 500            # Pinterestの説明文上限
PREFERRED_IMAGE = 1       # 1=メイン画像, 2=追加画像1枚目(-m-02), 3=-m-03 …（着用画像の番号に合わせて変更）
UTM = {"utm_source": "pinterest", "utm_medium": "social", "utm_campaign": "rss_new_arrivals"}

# (タイトルに含まれる語, ファイル名, 対応ボード) 上から順に判定。長い語・紛らわしい語を先に
CATEGORIES = [
    ("ルームシューズ", "room-shoes", "洗えるルームシューズ"),
    ("レインシューズ", "rain", "レインシューズ"),
    ("レインブーツ", "rain", "レインシューズ"),
    ("パンプス", "pumps", "洗えるパンプス"),
    ("サンダル", "sandals", "洗えるサンダル"),
    ("ローファー", "loafers", "洗えるローファー"),
    ("モカシン", "moccasins", "洗えるモカシン"),
    ("スニーカー", "sneakers", "洗えるスニーカー"),
    ("ブーツ", "boots", "洗えるブーツ"),
]
OTHER_CATEGORY = ("other", "その他")

BRAND_NAMES = ["クロールバリエ", "COULEUR VARIE", "バスクラフト", "BATH CRAFT", "newmo", "elevage"]
ITEM_NO_PATTERN = re.compile(r"^\s*(№|No[\.,、]?|品番)\s*[0-9A-Za-z\-]+(?:\s*[/／]\s*[0-9A-Za-z\-]+)*\s*", re.IGNORECASE)


# 商品名から外すもの：(レディース 女性用…)などの括弧書き、※返品対応可 などの注記
PAREN_PATTERN = re.compile(r"\s*[（(\[［〔][^）)\]］〕]*[）)\]］〕]?")
NOTE_PATTERN = re.compile(r"\s*※[^※]*※?")
# 特徴として扱わない販促ワード（タイトルの【】に入っていることがある）
PROMO_PATTERN = re.compile(r"新作|新色|人気|返品|送料|セール|SALE|限定|再入荷|OFF|ポイント|予約", re.IGNORECASE)


def clean_name(text):
    text = PAREN_PATTERN.sub("", text)
    text = NOTE_PATTERN.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ==========================================
# テキスト整形
# ==========================================
def split_title(title):
    """update_feed.py が作った『【特徴・特徴】 商品名 ブランド』を分解"""
    features, body = [], title.strip()
    m = re.match(r"^【(.*?)】\s*(.*)$", body)
    if m:
        features = [f for f in m.group(1).split("・") if f and not PROMO_PATTERN.search(f)]
        body = m.group(2)
    brand = ""
    for b in BRAND_NAMES:
        if body.endswith(b):
            brand, body = b, body[: -len(b)].strip()
            break
    return features, clean_name(body), brand


def build_title(features, name, brand):
    """検索されやすい『洗える＋アイテム名』を先頭に。特徴は2〜3個まで"""
    if "洗える" in features and "洗える" not in name:
        name = "洗える" + name
    others = [f for f in features if f != "洗える"][:3]
    title = name
    if others:
        title += "｜" + "・".join(others)
    if brand and len(title) + len(brand) + 1 <= TITLE_MAX:
        title += " " + brand
    return title[:TITLE_MAX]


def build_description(features, name, raw_desc):
    """冒頭に商品名と特徴を置き、品番から始まらないようにする"""
    body = ITEM_NO_PATTERN.sub("", raw_desc or "").strip()
    if "洗える" in features and "洗える" not in name:
        name = "洗える" + name
    others = [f for f in features if f != "洗える"]
    lead = f"{name}。"
    if others:
        lead += "・".join(others) + "。"
    desc = f"{lead} {body}".strip()
    return desc[:DESC_MAX].rstrip()


# ==========================================
# URL整形
# ==========================================
def strip_cache_param(url):
    """update_feed.py が付ける ?v=タイムスタンプ を除去（毎回URLが変わるのを防ぐ）"""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "v"]
    return urlunsplit(parts._replace(query=urlencode(query)))


def pick_image(row):
    images = [row.get("image_link", "")]
    images += [u for u in row.get("additional_image_link", "").split(",") if u]
    idx = PREFERRED_IMAGE - 1
    url = images[idx] if 0 <= idx < len(images) else images[0]
    return strip_cache_param(url)


def add_utm(link):
    parts = urlsplit(link)
    query = parse_qsl(parts.query)
    if not any(k.startswith("utm_") for k, _ in query):
        query += list(UTM.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def detect_category(name):
    for word, slug, board in CATEGORIES:
        if word in name:
            return slug, board
    return OTHER_CATEGORY


# ==========================================
# 初登場日の管理
# ==========================================
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


# ==========================================
# RSS出力
# ==========================================
def write_rss(path, channel_title, items):
    e = html.escape
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0">', "<channel>",
           f"  <title>{e(channel_title)}</title>", f"  <link>{SHOP_LINK}</link>",
           f"  <description>{e(channel_title)} 新作</description>"]
    for it in items:
        out += ["  <item>",
                f"    <title>{e(it['title'])}</title>",
                f"    <link>{e(it['link'])}</link>",
                f"    <description>{e(it['description'])}</description>",
                f'    <enclosure url="{e(it["image"])}" type="image/jpeg" length="0" />',
                f'    <guid isPermaLink="false">{e(it["id"])}</guid>',
                f"    <pubDate>{it['pub_date']}</pubDate>",
                "  </item>"]
    out += ["</channel>", "</rss>"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def main():
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"エラー: {INPUT_FILE} が見つかりません。")
        return

    today = date.today().isoformat()
    state = load_state()
    first_run = state is None
    if first_run:
        # 初回は既存商品をすべて「過去分」として登録し、一斉にピン化されるのを防ぐ
        state = {}
        print("初回実行：既存商品を登録済みとして扱います（次回の新作から配信）。")

    cutoff = (date.today() - timedelta(days=NEW_ONLY_DAYS)).isoformat()
    feeds = {}   # slug -> (board, [items])
    fallback = {}  # slug -> [(初登場日, item)]  新作が無いカテゴリ用の候補
    all_items = []

    for row in rows:
        item_id = row.get("id", "").strip()
        if not item_id:
            continue
        if item_id not in state:
            state[item_id] = "2000-01-01" if first_run else today

        availability = row.get("availability", "").strip().lower()
        if availability in ("out of stock", "在庫切れ"):
            continue
        features, name, brand = split_title(row.get("title", ""))
        slug, board = detect_category(name)
        first_seen = datetime.fromisoformat(state[item_id]).replace(hour=9, tzinfo=timezone.utc)

        item = {
            "id": item_id,
            "title": build_title(features, name, brand),
            "description": build_description(features, name, row.get("description", "")),
            "link": add_utm(row.get("link", "")),
            "image": pick_image(row),
            "pub_date": format_datetime(first_seen),  # 毎回変わらないよう初登場日で固定
        }
        if state[item_id] < cutoff:
            # 新作期間を過ぎた商品は、カテゴリが空になったときの予備としてだけ保持
            fallback.setdefault(slug, []).append((state[item_id], item))
            continue
        feeds.setdefault(slug, (board, []))[1].append(item)
        all_items.append(item)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    slugs = {slug for _, slug, _ in CATEGORIES} | {OTHER_CATEGORY[0]}
    boards = {slug: board for _, slug, board in CATEGORIES}
    boards[OTHER_CATEGORY[0]] = OTHER_CATEGORY[1]
    for slug in sorted(slugs):
        board, items = feeds.get(slug, (boards[slug], []))
        if not items and fallback.get(slug):
            # 初登場日が新しい順（同日ならフィードの並び順）で予備を載せる
            cands = sorted(fallback[slug], key=lambda x: x[0], reverse=True)
            items = [it for _, it in cands[:FALLBACK_ITEMS]]
        write_rss(os.path.join(OUTPUT_DIR, f"pinterest_{slug}.xml"), f"{SHOP_TITLE} {board}", items)
        print(f"  {slug:<11} → {board}: {len(items)}件")

    write_rss(ALL_FEED_FILE, SHOP_TITLE, all_items)
    save_state(state)
    print(f"Pinterest用フィードを生成しました（新作{len(all_items)}件）。")


if __name__ == "__main__":
    main()
