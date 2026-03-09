"""Setup script for LiveKit SIP trunk and dispatch rules.

Run: python setup_infra.py
Requires: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, TWILIO_PHONE_NUMBER
"""

import asyncio
import os

from dotenv import load_dotenv
from livekit import api

load_dotenv()


async def setup():
    lk = api.LiveKitAPI(
        os.getenv("LIVEKIT_URL", ""),
        os.getenv("LIVEKIT_API_KEY", ""),
        os.getenv("LIVEKIT_API_SECRET", ""),
    )

    phone = os.getenv("TWILIO_PHONE_NUMBER", "")
    if not phone:
        print("ERROR: Set TWILIO_PHONE_NUMBER in .env")
        return

    print(f"Setting up SIP infrastructure for {phone}...")

    # 1. Create inbound SIP trunk
    try:
        trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="FrontOffice Twilio Inbound",
                    numbers=[phone],
                )
            )
        )
        trunk_id = trunk.sip_trunk_id
        print(f"Created SIP inbound trunk: {trunk_id}")
    except Exception as e:
        print(f"SIP trunk creation: {e}")
        print("If trunk already exists, this is OK. Find trunk ID in LiveKit dashboard.")
        trunk_id = input("Enter existing trunk ID (or press Enter to skip): ").strip()
        if not trunk_id:
            return

    # 2. Create dispatch rule
    try:
        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                        room_prefix="call-",
                    ),
                ),
                trunk_ids=[trunk_id],
                name="FrontOffice Call Dispatch",
            )
        )
        print(f"Created dispatch rule: {rule.sip_dispatch_rule_id}")
    except Exception as e:
        print(f"Dispatch rule creation: {e}")

    # 3. Print Twilio TwiML Bin configuration
    # Note: Twilio Elastic SIP Trunking is not available on trial accounts.
    # Instead, use Twilio Programmable Voice with a TwiML Bin that dials
    # the LiveKit SIP URI directly.
    livekit_sip_uri = os.getenv("LIVEKIT_SIP_URI", "")
    if not livekit_sip_uri:
        print("\n⚠  LIVEKIT_SIP_URI not set in .env")
        print("   Find it at: https://cloud.livekit.io → Project Settings → SIP URI")
        print("   Example: sip:xxxxx.sip.livekit.cloud")
        livekit_sip_uri = input("   Enter your LiveKit SIP URI: ").strip()
        if not livekit_sip_uri:
            print("   Skipping TwiML instructions.")
            await lk.aclose()
            return

    # Extract host from sip:host format
    sip_host = livekit_sip_uri.replace("sip:", "")

    print("\n" + "=" * 60)
    print("TWILIO TWIML BIN CONFIGURATION")
    print("=" * 60)
    print("\n1. Create a TwiML Bin:")
    print("   Go to https://console.twilio.com/us1/develop/twiml-bins")
    print("   → Create new TwiML Bin → name it 'LiveKit SIP'")
    print("   → Paste this XML:\n")
    print(f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip>sip:{phone}@{sip_host}</Sip>
  </Dial>
</Response>""")
    print(f"\n2. Assign TwiML Bin to phone number {phone}:")
    print("   Go to https://console.twilio.com/us1/develop/phone-numbers/manage/incoming")
    print(f"   → Click {phone} → Voice Configuration")
    print("   → 'A call comes in' → select 'TwiML Bin' → select 'LiveKit SIP'")
    print("   → Save")
    print("\n" + "=" * 60)

    await lk.aclose()


if __name__ == "__main__":
    asyncio.run(setup())
