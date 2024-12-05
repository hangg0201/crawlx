from flask import Flask, request, render_template, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import jmespath
from playwright.sync_api import sync_playwright
import time
from threading import Thread
import json


def load_tweet_data(file_path="tweet_data.json"):
    """Đọc danh sách tweet từ tệp JSON."""
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        # Nếu tệp không tồn tại, trả về danh sách rỗng
        return []
    except json.JSONDecodeError:
        # Nếu tệp bị lỗi, trả về danh sách rỗng
        return []

def save_tweet_data(tweet_data_list, file_path="tweet_data.json"):
    """Ghi danh sách tweet vào tệp JSON."""
    with open(file_path, "w") as f:
        json.dump(tweet_data_list, f, indent=4)


app = Flask(__name__)

# Google Sheets Configuration
def update_google_sheet(sheet_name: str, worksheet_name: str, tweet_url: str, parsed_data: dict):
    # Kết nối với Google Sheets API
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)

    # Mở Google Sheet và worksheet
    sheet = client.open(sheet_name)
    worksheet = sheet.worksheet(worksheet_name)

    # Lấy tiêu đề từ hàng đầu tiên
    headers = worksheet.row_values(1)
    if not headers:
        # Nếu chưa có tiêu đề, tạo mới
        headers = ["Link", "Choice 1", "Choice 2", "Choice 3", "Choice 4", "Time Remaining"]
        worksheet.insert_row(headers, index=1)

    # Chuẩn bị dữ liệu cho từng cột
    choices = list(parsed_data["choices"].items())
    row_data = [tweet_url]
    for i in range(4):  # Xử lý tối đa 4 cột cho lựa chọn
        if i < len(choices):
            label, count = choices[i]
            row_data.append(f"{label}: {count}")
        else:
            row_data.append("")  # Nếu không đủ lựa chọn, để trống
    row_data.append(parsed_data["time_remaining"])

    # Thêm hàng mới vào sheet
    worksheet.append_row(row_data)

def update_google_sheet_row(sheet_name: str, worksheet_name: str, tweet_url: str, updated_data: dict):
    """
    Cập nhật một hàng cụ thể trong Google Sheet dựa trên URL.
    """
    # Kết nối với Google Sheets API
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)

    # Mở Google Sheet và worksheet
    sheet = client.open(sheet_name)
    worksheet = sheet.worksheet(worksheet_name)

    # Tìm hàng chứa URL
    cell = worksheet.find(tweet_url)
    if cell:
        # Nếu tìm thấy, cập nhật dữ liệu
        row = cell.row
        choices = list(updated_data["choices"].items())
        for i in range(4):  # Cập nhật tối đa 4 lựa chọn
            if i < len(choices):
                label, count = choices[i]
                worksheet.update_cell(row, i + 2, f"{label}: {count}")  # Cập nhật các cột "Choice 1, Choice 2,..."
            else:
                worksheet.update_cell(row, i + 2, "")  # Xóa dữ liệu cũ nếu không còn đủ lựa chọn
        # Cập nhật cột "Time Remaining"
        worksheet.update_cell(row, 6, updated_data["time_remaining"])
        print(f"Updated row {row} for URL: {tweet_url}")
    else:
        # Nếu không tìm thấy, thêm mới hàng
        print(f"URL {tweet_url} not found in Google Sheet. Adding a new row.")
        row_data = [tweet_url]
        choices = list(updated_data["choices"].items())
        for i in range(4):
            if i < len(choices):
                label, count = choices[i]
                row_data.append(f"{label}: {count}")
            else:
                row_data.append("")
        row_data.append(updated_data["time_remaining"])
        worksheet.append_row(row_data)


def update_sheets_periodically(sheet_name: str, worksheet_name: str):
    while True:
        if tweet_data_list:
            for tweet in tweet_data_list:
                try:
                    # Cập nhật lại dữ liệu tweet
                    tweet_data = scrape_tweet(tweet)
                    updated_data = parse_tweet(tweet_data, tweet)

                    # Cập nhật Google Sheet
                    update_google_sheet_row(sheet_name, worksheet_name, tweet, updated_data)
                    print("Update Successfully!")
                except Exception as e:
                    print(f"Failed to update {tweet}: {e}")
        
        # Chờ 10 phút trước khi cập nhật lại
        time.sleep(600)


# Twitter Scraper
def scrape_tweet(url: str) -> dict:
    _xhr_calls = []

    def intercept_response(response):
        if response.request.resource_type == "xhr":
            _xhr_calls.append(response)
        return response

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.on("response", intercept_response)
        page.goto(url)
        page.wait_for_selector("[data-testid='tweet']")
        tweet_calls = [f for f in _xhr_calls if "TweetResultByRestId" in f.url]
        for xhr in tweet_calls:
            data = xhr.json()
            return data['data']['tweetResult']['result']

def parse_tweet(data: dict, tweet_url: str) -> dict:
    poll_data = jmespath.search("card.legacy.binding_values", data) or []
    poll_choices = {}
    poll_end = None

    for poll_entry in poll_data:
        key, value = poll_entry["key"], poll_entry["value"]
        if "choice" in key and "label" in key:
            choice_number = key.replace("choice", "").replace("_label", "")
            if choice_number not in poll_choices:
                poll_choices[choice_number] = {"label": value["string_value"], "count": 0}
            else:
                poll_choices[choice_number]["label"] = value["string_value"]
        elif "choice" in key and "count" in key:
            choice_number = key.replace("choice", "").replace("_count", "")
            if choice_number not in poll_choices:
                poll_choices[choice_number] = {"label": "N/A", "count": int(value["string_value"])}
            else:
                poll_choices[choice_number]["count"] = int(value["string_value"])
        elif "end_datetime" in key:
            poll_end = value["string_value"]

    time_remaining = "Unknown"
    if poll_end:
        poll_end_dt = datetime.fromisoformat(poll_end.replace("Z", "+00:00"))
        now = datetime.now(poll_end_dt.tzinfo)  # Đảm bảo múi giờ đồng bộ
        delta = poll_end_dt - now
        if delta.total_seconds() > 0:
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            time_remaining = f"{hours}h {minutes}m"
        else:
            time_remaining = "Poll has ended"

    parsed_data = {
        "link": tweet_url,
        "choices": {v["label"]: v["count"] for v in poll_choices.values()},
        "time_remaining": time_remaining
    }

    return parsed_data


# Lấy danh sách tweet từ tệp JSON khi khởi chạy
tweet_data_list = load_tweet_data()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        tweet_url = request.form.get("tweet_url")
        sheet_name = "2024SeoulCon APAN Stars Award"
        worksheet_name = "Poll X"

        try:
            # Thêm URL mới vào Google Sheet
            tweet_data = scrape_tweet(tweet_url)
            parsed_data = parse_tweet(tweet_data, tweet_url)
            update_google_sheet(sheet_name, worksheet_name, tweet_url, parsed_data)

            # Thêm URL vào danh sách và lưu vào tệp JSON
            if tweet_url not in tweet_data_list:
                tweet_data_list.append(tweet_url)
                save_tweet_data(tweet_data_list)

            return render_template("index.html", message="Tweet added successfully!", success=True, data=parsed_data)
        except Exception as e:
            return render_template("index.html", message=f"Error: {e}", success=False)

    return render_template("index.html")


if __name__ == "__main__":
    # Chạy luồng cập nhật Google Sheets định kỳ
    sheet_name = "2024SeoulCon APAN Stars Award"
    worksheet_name = "Poll X"
    thread = Thread(target=update_sheets_periodically, args=(sheet_name, worksheet_name), daemon=True)
    thread.start()

    # Khởi động Flask
    app.run(debug=True)

