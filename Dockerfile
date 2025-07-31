FROM mcr.microsoft.com/playwright/python:v1.44.0
RUN python init_db.py
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# ✨ สร้าง database ก่อนรัน Gunicorn
RUN python -c 'from app import init_db; init_db()'

# 🚀 เริ่ม Gunicorn (Render จะเรียกอันนี้)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]
