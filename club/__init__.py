# club — tenant (club) configuration: club, location, branding, policy.
# The tenancy root. Every domain row elsewhere FKs to club.club(id).
from club.schema import init  # noqa: F401

__all__ = ["init"]
