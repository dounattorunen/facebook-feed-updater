import csv
import html
from email.utils import formatdate
import time

def main():
    input_file = "facebook_feed.csv"
    output_file = "pinterest_feed.xml"

    # RSSヘッダー（※ショップ名とURLは自社のものに変更してください）
    rss = '<?xml version="1.0" encoding="UTF-8"?>\n'
    rss += '<rss version="2.0">\n<channel>\n'
    rss += '  <title>あなたのショップ名</title>\n'
    rss += '  <link>https://www.your-shop-domain.com</link>\n'
    rss += '  <description>Pinterest用商品フィード</description>\n'
    
    pub_date = formatdate(time.time(), localtime=False)
    
    try:
        # update_feed.py が作ったCSVを読み込む
        with open(input_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = html.escape(row.get("title", ""))
                link = html.escape(row.get("link", ""))
                desc = html.escape(row.get("description", ""))
                image = html.escape(row.get("image_link", ""))
                
                rss += '  <item>\n'
                rss += f'    <title>{title}</title>\n'
                rss += f'    <link>{link}</link>\n'
                rss += f'    <description>{desc}</description>\n'
                rss += f'    <enclosure url="{image}" type="image/jpeg" length="0" />\n'
                rss += f'    <pubDate>{pub_date}</pubDate>\n'
                rss += '  </item>\n'
    except FileNotFoundError:
        print(f"エラー: {input_file} が見つかりません。")
        return
        
    rss += '</channel>\n</rss>'
    
    # XMLファイルとして保存
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(rss)
        
    print(f"Pinterest用フィードを {output_file} に生成しました。")

if __name__ == "__main__":
    main()
