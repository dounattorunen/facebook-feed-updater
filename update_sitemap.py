"""sitemap.xml（手作業で管理している bath-ec.com のサイトマップ）の商品URLを、公開中の商品に合わせて直す。

GitHub Actions（update.yml）から毎日実行する。
  ・今の sitemap.xml をサイトから読み込む
  ・futureshop の商品一覧（標準検索の全件表示）から、公開中で買える商品を集める
  ・一覧に無い商品URLは1件ずつ確かめ、ページが無い（404 = 非公開・削除）ものだけ消す
    （在庫切れで一覧に出ないだけの商品はページがあるので残す）
  ・一覧にあってサイトマップに無い商品URLを足す
  ・商品以外のURL（特集ページなど）は一切さわらない
変更があったときだけ sitemap_upload.xml を書き出す（アップロードはワークフロー側。リポジトリには残さない）。
変更内容は sitemap_changes.txt に書き、前の版は sitemap_backup/ に残す。
"""
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

SITE = "https://www.bath-ec.com"
SITEMAP_URL = SITE + "/sitemap.xml"
LIST_URL = SITE + "/fs/bath/GoodsSearchList.html?_e_k=%82%60&keyword=&pageno={}"
UA = {"User-Agent": "Mozilla/5.0 (bath-sitemap-updater)"}
ITEM_URL = re.compile(r"^https://www\.bath-ec\.com/fs/bath/([0-9A-Za-z][0-9A-Za-z\-]*)$")
MIN_LISTED = 100          # 一覧から集めた商品がこれより少なければ失敗とみなし、何もしない
MAX_REMOVE = 30           # 1回で消すのはここまで（これより多ければ異常とみなして止める）


def get(url, timeout=30):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            time.sleep(3 * (i + 1))
    return None, b""


def listed_items():
    """標準検索の全件一覧（在庫のある公開商品）から商品番号を集める"""
    ids, page = [], 1
    while page <= 40:
        status, body = get(LIST_URL.format(page))
        if status != 200:
            raise RuntimeError(f"商品一覧 {page}ページ目を読めませんでした（{status}）")
        html = body.decode("cp932", errors="replace")
        found = re.findall(r'href="https://www\.bath-ec\.com/fs/bath/(?:[a-z_0-9]+/)?([0-9][0-9A-Za-z\-]*)"', html)
        new = [x for x in dict.fromkeys(found) if x.lower() not in {i.lower() for i in ids}]
        if not new:
            break
        ids += new
        page += 1
        time.sleep(1)
    return ids


def main():
    status, body = get(SITEMAP_URL)
    if status != 200 or not body:
        print(f"::warning title=sitemap::sitemap.xml を読めませんでした（{status}）。何もしません")
        return
    xml = body.decode("utf-8")
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    in_map = {}
    for u in locs:
        m = ITEM_URL.match(u)
        if m:
            in_map[m.group(1).lower()] = u

    try:
        listed = listed_items()
    except Exception as e:
        print(f"::warning title=sitemap::{e}。何もしません")
        return
    if len(listed) < MIN_LISTED:
        print(f"::warning title=sitemap::商品一覧が {len(listed)} 件しか取れなかったため、何もしません")
        return
    listed_l = {x.lower(): x for x in listed}

    # 一覧に無い商品URLは、ページがあるか確かめる（404 だけ消す）
    remove, checked = [], 0
    for key, u in in_map.items():
        if key in listed_l:
            continue
        st, _ = get(u, timeout=20)
        checked += 1
        if st == 404:
            remove.append(u)
        time.sleep(0.5)
    add = [f"{SITE}/fs/bath/{listed_l[k]}" for k in listed_l if k not in in_map]

    if len(remove) > MAX_REMOVE:
        print(f"::warning title=sitemap::消す候補が {len(remove)} 件と多すぎるため、何もしません（サイトの不調の可能性）")
        return
    print(f"sitemap.xml: 商品URL {len(in_map)}件 / 公開中で在庫のある商品 {len(listed)}件 / 一覧に無く確認した {checked}件")
    if not remove and not add:
        print("sitemap.xml: 変更はありません")
        return

    new_xml = xml
    for u in remove:
        new_xml = re.sub(r"[ \t]*<url>\s*<loc>\s*" + re.escape(u) + r"\s*</loc>.*?</url>[ \t]*\r?\n?", "", new_xml, flags=re.S)
    if add:
        lines = "".join(f"  <url><loc>{u}</loc></url>\n" for u in add)
        new_xml = re.sub(r"</urlset>\s*$", lines + "</urlset>\n", new_xml)
    if "</urlset>" not in new_xml or new_xml.count("<url>") != len(locs) - len(remove) + len(add):
        print("::warning title=sitemap::書き換えの結果が想定と合わないため、何もしません")
        return

    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    os.makedirs("sitemap_backup", exist_ok=True)
    with open(f"sitemap_backup/sitemap_{now:%Y%m%d_%H%M}.xml", "w", encoding="utf-8") as f:
        f.write(xml)
    with open("sitemap_upload.xml", "w", encoding="utf-8") as f:
        f.write(new_xml)
    # 前の版は直近30回分だけ残す
    olds = sorted(x for x in os.listdir("sitemap_backup") if x.startswith("sitemap_"))
    for x in olds[:-30]:
        os.remove(os.path.join("sitemap_backup", x))
    with open("sitemap_changes.txt", "a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} 削除 {len(remove)} / 追加 {len(add)}\n")
        for u in remove:
            f.write(f"  - {u}\n")
        for u in add:
            f.write(f"  + {u}\n")
    short = lambda us: [x.rsplit('/', 1)[1] for x in us][:30] + (["…"] if len(us) > 30 else [])
    print(f"sitemap.xml を更新しました：削除 {len(remove)}件 {short(remove)} / 追加 {len(add)}件 {short(add)}")


if __name__ == "__main__":
    main()
