# iam — identity, membership, roles, profiles (maps Clerk users -> clubs).
# Identity (login) lives in Clerk; iam.* is the platform's view of who-belongs-where.
from iam.schema import init  # noqa: F401

__all__ = ["init"]
