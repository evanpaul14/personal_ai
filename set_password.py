#!/usr/bin/env python3
"""Set the login password for personal_ai. Writes hash to .auth_hash.

Usage:
  python3 set_password.py
"""
import sys
from auth import save_hash, AUTH_HASH_FILE


def main():
    if len(sys.argv) != 1:
        print("Usage: python3 set_password.py")
        print("For security, passing passwords via CLI arguments is disabled.")
        sys.exit(1)

    try:
        import getpass
        print("personal_ai — set login password")
        print()
        pw = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")
    except (EOFError, OSError):
        print("Error: interactive input not available.")
        print("Usage: python3 set_password.py")
        sys.exit(1)

    if len(pw) < 10:
        print("Error: password must be at least 10 characters.")
        sys.exit(1)
    if pw != confirm:
        print("Error: passwords do not match.")
        sys.exit(1)

    save_hash(pw)
    print(f"Password set. Hash stored in {AUTH_HASH_FILE}")
    print(f"Make sure {AUTH_HASH_FILE} is in your .gitignore!")


if __name__ == "__main__":
    main()
