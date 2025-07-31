# init_db.py
from app import db

def init_db():
    with db.engine.connect() as conn:
        db.create_all()

if __name__ == "__main__":
    init_db()
