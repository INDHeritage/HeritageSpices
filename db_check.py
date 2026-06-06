import app
from sqlalchemy import text

with app.app.app_context():
    res = app.db.session.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'product'")).fetchall()
    print("Product Schema:", res)
    
    res = app.db.session.execute(text("SELECT * FROM product")).fetchall()
    print("Product Data:", res)
