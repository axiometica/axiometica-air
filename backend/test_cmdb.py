#!/usr/bin/env python3
"""Test CMDB service directly."""
import sys
sys.path.insert(0, '/app/src')

from agentic_os.services.cmdb import get_cmdb

print("[TEST] Getting CMDB service...")
cmdb = get_cmdb()
print(f"[TEST] CMDB service: {cmdb}")
print(f"[TEST] Driver: {cmdb.driver}")

print("\n[TEST] Querying payment-service...")
result = cmdb.get_resource_info("payment-service")
print(f"[TEST] Result: {result}")

print("\n[TEST] Querying yes-service...")
result2 = cmdb.get_resource_info("yes-service")
print(f"[TEST] Result: {result2}")

print("\nTest complete.")
