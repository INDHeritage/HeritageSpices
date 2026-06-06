import os
import pandas as pd
from app import app, db, User, Visit, CustomerDetail
from datetime import datetime

def migrate():
    with app.app_context():
        print("Creating tables...")
        db.create_all()

        if os.path.exists('users.csv'):
            print("Migrating users...")
            df_users = pd.read_csv('users.csv')
            for _, row in df_users.iterrows():
                # Avoid duplicates
                if not User.query.filter_by(email=row['email']).first():
                    u = User(
                        name=row.get('name', 'Unknown'),
                        email=row['email'],
                        picture=row.get('picture', '')
                    )
                    db.session.add(u)
            db.session.commit()
            print("Users migrated.")

        if os.path.exists('customer_details.csv'):
            print("Migrating customer details...")
            df_cust = pd.read_csv('customer_details.csv')
            for _, row in df_cust.iterrows():
                if not CustomerDetail.query.filter_by(email=row['email']).first():
                    c = CustomerDetail(
                        email=row['email'],
                        phone=str(row.get('phone', '')),
                        location=str(row.get('location', '')),
                        interest=str(row.get('interest', ''))
                    )
                    db.session.add(c)
            db.session.commit()
            print("Customer details migrated.")

        if os.path.exists('visits.csv'):
            print("Migrating visits (might take a moment)...")
            df_visits = pd.read_csv('visits.csv')
            visits_to_add = []
            for _, row in df_visits.iterrows():
                try:
                    ts = datetime.fromisoformat(row['timestamp'])
                except:
                    ts = datetime.utcnow()
                
                v = Visit(
                    timestamp=ts,
                    ip=str(row.get('ip', '')),
                    user_agent=str(row.get('user_agent', '')),
                    email=str(row.get('email', 'Guest'))
                )
                visits_to_add.append(v)
            
            # Bulk insert for speed
            if visits_to_add:
                db.session.bulk_save_objects(visits_to_add)
                db.session.commit()
            print(f"Migrated {len(visits_to_add)} visits.")
            
        print("Migration complete!")

if __name__ == '__main__':
    migrate()
