FROM mcr.microsoft.com/playwright/python:v1.44.0

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# ลบคำสั่งนี้ออก เพื่อหลีกเลี่ยงการสร้าง database ในตอน build
# RUN python -c 'from app import init_db; init_db()'

# คำสั่งนี้คือ Gunicorn จะเรียกไฟล์ app.py และรัน Flask app
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]
