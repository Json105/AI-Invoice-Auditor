"""Generate synthetic mock invoice and travel receipt images for testing."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def create_b2b_invoice_image(out_path: Path):
    """Draw a synthetic Taiwan Uniform Electronic Invoice image."""
    img = Image.new("RGB", (600, 850), color="#FFFFFF")
    draw = ImageDraw.Draw(img)

    # Outer border
    draw.rectangle([20, 20, 580, 830], outline="#333333", width=2)

    # Header
    draw.text((220, 40), "電 子 發 票 證 明 聯", fill="#000000")
    draw.text((250, 70), "115 年 09-10 月", fill="#000000")
    draw.text((210, 100), "AB-88996633", fill="#000000")

    draw.line([30, 140, 570, 140], fill="#888888", width=1)

    # Invoice Details
    y = 160
    details = [
        "開立時間: 2026-09-01 14:32:10",
        "買方統編: 22099131 (台積電)",
        "賣方統編: 04541302 (鴻海精密)",
        "賣方名稱: 鴻海精密工業股份有限公司",
        "幣別: TWD 新台幣",
        "--------------------------------------------------",
        "品名: 高階伺服器散熱模組 (Server Cooling Pack)",
        "數量: 5.0   單價: 10,000   小計: 50,000",
        "--------------------------------------------------",
        "銷售額 (未稅): NT$ 50,000",
        "營業稅 (5%):    NT$  2,500",
        "總計 (含稅):    NT$ 52,500"
    ]
    for line in details:
        draw.text((50, y), line, fill="#222222")
        y += 30

    # Draw simulated barcode and QR codes
    draw.rectangle([50, 540, 550, 580], fill="#333333")
    draw.rectangle([70, 610, 230, 770], outline="#222222", width=2)
    draw.text((90, 680), "[ QR Code 左 ]", fill="#666666")

    draw.rectangle([370, 610, 530, 770], outline="#222222", width=2)
    draw.text((390, 680), "[ QR Code 右 ]", fill="#666666")

    draw.text((180, 795), "** 營業人銷貨退回或折讓證明請妥善保存 **", fill="#888888")

    img.save(out_path)
    print(f"Generated: {out_path}")


def create_thsr_ticket_image(out_path: Path):
    """Draw a synthetic Taiwan High Speed Rail ticket image."""
    img = Image.new("RGB", (700, 380), color="#FFFAEE") # Light ivory
    draw = ImageDraw.Draw(img)

    # Orange header banner
    draw.rectangle([0, 0, 700, 60], fill="#FF6600")
    draw.text((230, 20), "台灣高鐵 Taiwan High Speed Rail", fill="#FFFFFF")

    # Outer border
    draw.rectangle([10, 10, 690, 370], outline="#FF6600", width=3)

    # Route
    draw.text((60, 90), "台北 TAIPEI", fill="#000000")
    draw.text((270, 90), "➔ ➔ ➔", fill="#FF6600")
    draw.text((430, 90), "左營 ZUOYING", fill="#000000")

    draw.line([40, 140, 660, 140], fill="#CCCCCC", width=1)

    # Ticket info
    draw.text((60, 160), "搭乘日期: 2026-09-02", fill="#333333")
    draw.text((360, 160), "車次: 0609 次 (08:15 開)", fill="#333333")

    draw.text((60, 200), "座位: 03 車 11A 號 (商務/標準)", fill="#333333")
    draw.text((360, 200), "票號: THSR-291048201948", fill="#333333")

    draw.text((60, 240), "買方統編: 22099131", fill="#333333")
    draw.text((360, 240), "票價: NT$ 1,490 元 (內含營業稅)", fill="#D9534F")

    # Barcode
    draw.rectangle([60, 290, 640, 330], fill="#333333")
    draw.text((260, 340), "* 購票證明 / 限當日當次車有效 *", fill="#888888")

    img.save(out_path)
    print(f"Generated: {out_path}")


def create_taxi_receipt_image(out_path: Path):
    """Draw a synthetic Taxi paper receipt image."""
    img = Image.new("RGB", (450, 650), color="#FBFBFA")
    draw = ImageDraw.Draw(img)

    draw.rectangle([15, 15, 435, 635], outline="#777777", width=1)

    # Header
    draw.text((140, 40), "台灣大車隊 55688", fill="#000000")
    draw.text((150, 70), "乘 車 證 明 單", fill="#000000")

    draw.line([30, 105, 420, 105], fill="#AAAAAA", width=1)

    y = 130
    lines = [
        "車牌號碼: TDC-8821",
        "司機姓名: 林運轉",
        "乘車日期: 2026-09-02",
        "上車時間: 14:20",
        "下車時間: 14:50",
        "上車地點: 台北車站 (忠孝西路)",
        "下車地點: 內湖科技園區 (瑞光路)",
        "行駛里程: 9.8 公里",
        "--------------------------------------",
        "車資總額: NT$ 350 元",
        "付款方式: 信用卡/企業簽單",
        "統一編號: 28495018 (車隊統編)",
        "--------------------------------------",
        "客服專線: (02) 4058-8888",
        "謝謝惠顧，祝您洽公順利！"
    ]

    for l in lines:
        draw.text((50, y), l, fill="#222222")
        y += 28

    img.save(out_path)
    print(f"Generated: {out_path}")


if __name__ == "__main__":
    out_dir = Path(__file__).parent
    create_b2b_invoice_image(out_dir / "sample_b2b_invoice.png")
    create_thsr_ticket_image(out_dir / "sample_thsr_ticket.png")
    create_taxi_receipt_image(out_dir / "sample_taxi_receipt.png")
