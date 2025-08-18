#!/usr/bin/env python3

import uuid
from sei_publisher import SeiPublisher


def verify_uuid():
    """Verify that the SEI UUID is correctly set"""

    # Expected UUID from the specification
    expected_uuid_str = "9e504ea5-ee5a-4f02-949f-b033a3768da2"
    expected_bytes = bytes([0x9E, 0x50, 0x4E, 0xA5, 0xEE, 0x5A, 0x4F, 0x02, 0x94, 0x9F, 0xB0, 0x33, 0xA3, 0x76, 0x8D, 0xA2])

    # Get the UUID from SeiPublisher
    sei_publisher = SeiPublisher()
    actual_bytes = sei_publisher.SEND_SEI_UUID

    print("🔍 UUID Verification")
    print("=" * 50)
    print(f"Expected UUID string: {expected_uuid_str}")
    print(f"Expected bytes: {expected_bytes.hex()}")
    print(f"Actual bytes:   {actual_bytes.hex()}")
    print()

    # Verify they match
    if actual_bytes == expected_bytes:
        print("✅ UUID matches specification!")

        # Convert bytes back to UUID string for verification
        uuid_obj = uuid.UUID(bytes=actual_bytes)
        actual_uuid_str = str(uuid_obj)

        print(f"✅ Converted back to string: {actual_uuid_str}")

        if actual_uuid_str == expected_uuid_str:
            print("✅ Round-trip conversion successful!")
        else:
            print(f"❌ Round-trip failed: {actual_uuid_str} != {expected_uuid_str}")

    else:
        print("❌ UUID does not match specification!")
        print(f"   Expected: {expected_bytes.hex()}")
        print(f"   Actual:   {actual_bytes.hex()}")

    print()
    print("📋 JavaScript Array Format:")
    js_array = ", ".join([f"0x{b:02x}" for b in actual_bytes])
    print(f"[{js_array}]")


if __name__ == "__main__":
    verify_uuid()
