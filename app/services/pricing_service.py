from __future__ import annotations

PROVIDER_COSTS = {
    "stt": {
        "sarvam": 0.0020,
        "deepgram": 0.0040,
        "azure": 0.0030,
    },
    "tts": {
        "sarvam": 0.0020,
        "smallest": 0.0030,
        "elevenlabs": 0.0300,
        "cartesia": 0.0050,
    },
    "llm": {
        "openai/gpt-4o-mini": 0.0030,
        "openai/gpt-4o": 0.0150,
        "openai/gpt-4.1-mini": 0.0030,
        "openai/gpt-4.1": 0.0120,
        "anthropic/claude-3-haiku-20240307": 0.0020,
        "anthropic/claude-sonnet-4": 0.0200,
        "openai/gpt-4.1-nano": 0.0010,
    },
    "telephony": {
        "plivo": 0.0100,
        "exotel": 0.0120,
        "twilio": 0.0140,
    },
}

INR_RATE = 83.0
BOLNA_COST = 0.0790


def calculate_agent_pricing(agent) -> dict:
    stt = PROVIDER_COSTS["stt"].get(agent.stt_provider, 0.004)

    dual_tts = bool(
        getattr(agent, "tts_provider_english", None)
        and getattr(agent, "tts_provider_hindi", None)
    )
    if dual_tts:
        tts_en = PROVIDER_COSTS["tts"].get(agent.tts_provider_english, 0.003)
        tts_hi = PROVIDER_COSTS["tts"].get(agent.tts_provider_hindi, 0.002)
        tts = round((tts_en + tts_hi) / 2, 4)
    else:
        tts = PROVIDER_COSTS["tts"].get(agent.tts_provider, 0.002)

    llm = PROVIDER_COSTS["llm"].get(agent.llm_model, 0.003)
    tel = PROVIDER_COSTS["telephony"].get(agent.telephony_provider, 0.010)

    total = stt + tts + llm + tel
    total_inr = round(total * INR_RATE, 2)
    monthly_1000 = round(total_inr * 1000)
    savings_pct = round((BOLNA_COST - total) / BOLNA_COST * 100)

    return {
        "total_usd": round(total, 4),
        "total_inr": total_inr,
        "monthly_1000_mins_inr": monthly_1000,
        "savings_vs_bolna_pct": savings_pct,
        "dual_tts_enabled": dual_tts,
        "breakdown": {
            "stt_usd": stt,
            "tts_usd": tts,
            "llm_usd": llm,
            "telephony_usd": tel,
            "platform_usd": 0.0,
        },
        "breakdown_pct": {
            "stt": round(stt / total * 100),
            "tts": round(tts / total * 100),
            "llm": round(llm / total * 100),
            "telephony": round(tel / total * 100),
            "platform": 0,
        },
    }
