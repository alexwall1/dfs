"""Seed script - creates default admin user and number series."""

import os
import sys
import time

from app import create_app, db
from app.models import User, Nummerserie


def seed():
    app = create_app()
    with app.app_context():
        # Wait for DB to be ready (in Docker)
        for attempt in range(10):
            try:
                db.create_all()
                break
            except Exception:
                print(f"Waiting for database... (attempt {attempt + 1})")
                time.sleep(2)
        else:
            print("Could not connect to database.")
            sys.exit(1)

        # Create admin user if not exists
        if not User.query.filter_by(username="admin").first():
            admin_password = os.environ.get("ADMIN_PASSWORD")
            if not admin_password:
                try:
                    with open("/run/secrets/admin_password") as f:
                        admin_password = f.read().strip()
                except OSError:
                    pass
            if not admin_password:
                print("ADMIN_PASSWORD saknas: sätt miljövariabeln eller secrets/admin_password.txt")
                sys.exit(1)
            admin = User(
                username="admin",
                full_name="Systemadministratör",
                email="admin@example.com",
                role="admin",
                maste_byta_losenord=True,
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            print("Created admin user (admin / ***)")

        # Create default number series for current year
        from datetime import datetime, timezone

        year = datetime.now(timezone.utc).year
        if not Nummerserie.query.filter_by(prefix="DNR", year=year).first():
            serie = Nummerserie(prefix="DNR", year=year, current_number=0)
            db.session.add(serie)
            print(f"Created number series DNR-{year}")

        db.session.commit()
        print("Seed complete.")


if __name__ == "__main__":
    seed()
