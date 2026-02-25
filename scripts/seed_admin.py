"""
Seed the initial admin user via Supabase Auth.

Usage:
    python -m scripts.seed_admin

This will create an admin user in Supabase Auth, which triggers
the auto-profile creation via the database trigger.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.supabase_client import get_supabase_admin_client


def main():
    email = input("Admin email: ").strip()
    password = input("Admin password: ").strip()
    full_name = input("Full name: ").strip() or "Admin"

    if not email or not password:
        print("Email and password are required.")
        sys.exit(1)

    client = get_supabase_admin_client()

    try:
        response = client.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {
                "full_name": full_name,
                "role": "admin",
            },
        })
        print(f"Admin user created successfully!")
        print(f"  ID: {response.user.id}")
        print(f"  Email: {response.user.email}")
        print(f"  Role: admin")
        print(f"\nThe profile was auto-created via database trigger.")
        print(f"You can now login at POST /api/v1/auth/login")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
