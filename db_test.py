import sqlite3
conn = sqlite3.connect(r"E:\AI\python\ocr\lab_results.db")
for row in conn.execute("SELECT * FROM lab_results"):
    print(row)
conn.close()