"""
縦長ピン画像（1000x1500・生成りの帯）と、Pinterest「ピンの一括作成」用CSVを作る。
facebook_feed.csv（update_feed.py の出力）を読み、まだCSV投稿していない在庫あり商品から
1日 PER_DAY 件ずつ公開日時を割り振る。
"""
import csv
import io
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

from generate_pinterest import (split_title, build_title, build_description,
                                detect_category, strip_cache_param)

# ==========================================
# 設定
# ==========================================
INPUT_FILE = "facebook_feed.csv"
IMAGE_DIR = "pin_images"                       # 生成画像の保存先（FTPの pinterest/img/ に上げる）
IMAGE_BASE_URL = "https://www.bath-ec.com/pinterest/img/"
OUTPUT_CSV = "pinterest_bulk.csv"              # Pinterestにアップロードするファイル
DONE_FILE = "pinterest_csv_done.json"          # CSVに載せた商品の記録（要コミット）

PER_DAY = 2                                    # 1日に公開する件数
DAYS = 28                                      # 何日分を1回のCSVに入れるか（予約は30日先まで）
PUBLISH_HOURS = [12, 20]                       # 公開する時刻（日本時間）。CSVにはUTCに変換して出力
SHOP_NAME = "BATH ONLINE SHOP"
UTM = "utm_source=pinterest&utm_medium=social&utm_campaign=csv_vertical"

# デザイン（生成り）
W, H, BAND = 1000, 1500, 250
BG, INK, SUB = (244, 240, 233), (51, 45, 40), (120, 110, 100)
FONT_DIR = "/usr/share/fonts/opentype/noto/"
FONT_BOLD = FONT_DIR + "NotoSansCJK-Bold.ttc"
FONT_REG = FONT_DIR + "NotoSansCJK-Regular.ttc"


def font(path, size):
    return ImageFont.truetype(path, size, index=0)  # index 0 = 日本語(JP)


def fit_font(draw, text, path, size, min_size, max_w):
    """幅に収まるまで文字を小さくする。最小でも収まらなければ末尾を…で省略"""
    while size > min_size and draw.textlength(text, font=font(path, size)) > max_w:
        size -= 2
    f = font(path, size)
    while draw.textlength(text, font=f) > max_w and len(text) > 1:
        text = text[:-2] + "…"
    return text, f


def draw_center(draw, y, text, f, fill):
    w = draw.textlength(text, font=f)
    draw.text(((W - w) / 2, y), text, font=f, fill=fill)


def make_image(src_url, headline, subline, footer, out_path):
    with urllib.request.urlopen(src_url, timeout=20) as r:
        src = Image.open(io.BytesIO(r.read())).convert("RGB")
    # 正方形でない画像は中央を正方形に切り抜く
    s = min(src.size)
    left, top = (src.width - s) // 2, (src.height - s) // 2
    src = src.crop((left, top, left + s, top + s)).resize((W, W), Image.LANCZOS)

    im = Image.new("RGB", (W, H), BG)
    im.paste(src, (0, BAND))
    d = ImageDraw.Draw(im)
    t, f = fit_font(d, headline, FONT_BOLD, 52, 38, W - 100)
    draw_center(d, 70, t, f, INK)
    if subline:
        t, f = fit_font(d, subline, FONT_REG, 36, 28, W - 100)
        draw_center(d, 155, t, f, SUB)
    t, f = fit_font(d, footer, FONT_REG, 34, 26, W - 100)
    draw_center(d, BAND + W + 80, t, f, SUB)
    im.save(out_path, "JPEG", quality=88)


def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    done = {}
    if os.path.exists(DONE_FILE):
        with open(DONE_FILE, "r", encoding="utf-8") as f:
            done = json.load(f)

    os.makedirs(IMAGE_DIR, exist_ok=True)
    limit = PER_DAY * DAYS
    start = datetime.now(timezone(timedelta(hours=9))).date() + timedelta(days=1)  # 明日から
    out_rows = []

    for row in rows:
        if len(out_rows) >= limit:
            break
        item_id = row.get("id", "").strip()
        if not item_id or item_id in done:
            continue
        if row.get("availability", "").strip().lower() in ("out of stock", "在庫切れ"):
            continue

        features, name, brand = split_title(row.get("title", ""))
        slug, board = detect_category(name)
        if slug == "other":
            continue  # 靴以外はボードが決まっていないので対象外

        headline = ("洗える" + name) if ("洗える" in features and "洗える" not in name) else name
        subline = "・".join([x for x in features if x != "洗える"][:3])
        footer = f"{brand} ｜ {SHOP_NAME}" if brand else SHOP_NAME
        img_name = f"{item_id}.jpg"
        try:
            make_image(strip_cache_param(row.get("image_link", "")), headline, subline,
                       footer, os.path.join(IMAGE_DIR, img_name))
        except Exception as e:
            print(f"  画像生成スキップ {item_id}: {e}")
            continue

        n = len(out_rows)
        day = start + timedelta(days=n // PER_DAY)
        hour = PUBLISH_HOURS[n % PER_DAY % len(PUBLISH_HOURS)]
        # Pinterestの公開日時はUTC指定なので、日本時間から9時間引く
        publish_utc = datetime(day.year, day.month, day.day, hour) - timedelta(hours=9)
        link = row.get("link", "")
        link += ("&" if "?" in link else "?") + UTM

        out_rows.append({
            "Title": build_title(features, name, brand),
            "Media URL": IMAGE_BASE_URL + img_name,
            "Pinterest board": board,
            "Thumbnail": "",
            "Description": build_description(features, name, row.get("description", "")),
            "Link": link,
            "Publish date": publish_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            "Keywords": ",".join(features),
        })
        done[item_id] = day.isoformat()
        print(f"  {item_id} → {board} / {day} {hour}時")

    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Title", "Media URL", "Pinterest board", "Thumbnail",
                                          "Description", "Link", "Publish date", "Keywords"])
        w.writeheader()
        w.writerows(out_rows)
    with open(DONE_FILE, "w", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=0, sort_keys=True)
    print(f"{len(out_rows)}件のピンを {OUTPUT_CSV} に出力しました。")


if __name__ == "__main__":
    main()
