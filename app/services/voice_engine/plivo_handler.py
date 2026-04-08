import logging
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

import plivo
from app.config import get_settings

logger = logging.getLogger(__name__)


def _xml_safe(text: str) -> str:
    return xml_escape(text or "", {'"': "&quot;", "'": "&apos;"})


class PlivoHandler:

    def __init__(self):
        self._client = None

    @property
    def client(self):
        """Lazy-initialize Plivo client on first use to avoid env race at import."""
        if self._client is None:
            settings = get_settings()
            self._client = plivo.RestClient(
                auth_id=settings.plivo_auth_id,
                auth_token=settings.plivo_auth_token,
            )
        return self._client

    @property
    def phone_number(self):
        return get_settings().plivo_phone_number

    @property
    def backend_url(self):
        return get_settings().backend_url

    async def make_call(
        self,
        to_number: str,
        call_id: str,
        agent_id: str,
        lead_id: str,
        lead_name: str = "there",
        time_limit: int = 600,
        ring_timeout: int = 30,
    ) -> dict:
        """Initiate outbound call to lead. time_limit/ring_timeout in seconds."""
        try:
            answer_params = urlencode({
                "call_id": call_id,
                "agent_id": agent_id,
                "lead_id": lead_id,
                "lead_name": lead_name,
            })
            answer_url = f"{self.backend_url}/api/v1/voice/answer?{answer_params}"

            hangup_params = urlencode({"call_id": call_id})
            hangup_url = f"{self.backend_url}/api/v1/voice/hangup?{hangup_params}"

            logger.info(
                "PLIVO_MAKE_CALL call_id=%s to=%s from=%s agent_id=%s "
                "answer_url=%s hangup_url=%s",
                call_id, to_number, self.phone_number, agent_id,
                answer_url, hangup_url,
            )

            response = self.client.calls.create(
                from_=self.phone_number,
                to_=to_number,
                answer_url=answer_url,
                answer_method="POST",
                hangup_url=hangup_url,
                hangup_method="POST",
                time_limit=time_limit,
                ring_timeout=ring_timeout,
            )

            return {
                "success": True,
                "plivo_call_uuid": response["request_uuid"],
                "call_id": call_id,
                "to": to_number,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "call_id": call_id,
            }

    def generate_answer_xml(
        self,
        call_id: str,
        welcome_message: str = "",
        stream_token: str = "",
    ) -> str:
        """Plivo XML when lead picks up — connects authenticated WebSocket.

        Welcome message is no longer spoken via Polly here; it is sent as
        the first WS frame using the agent's configured TTS.
        """
        host = self.backend_url.replace("https://", "").replace("http://", "")
        stream_url = f"wss://{host}/api/v1/voice/stream/{call_id}"
        if stream_token:
            stream_url = f"{stream_url}?token={stream_token}"

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Stream bidirectional="true" keepCallAlive="true" streamTimeout="600" contentType="audio/x-mulaw;rate=8000" audioTrack="inbound">
        {stream_url}
    </Stream>
</Response>"""
        return xml

    def generate_hangup_xml(
        self,
        message: str = "Thank you. Goodbye!",
    ) -> str:
        """Generate XML to end the call gracefully."""
        safe_message = _xml_safe(message)
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak>{safe_message}</Speak>
    <Hangup/>
</Response>"""
        return xml

    def verify_signature(
        self,
        url: str,
        params: dict,
        signature: str,
    ) -> bool:
        """Verify Plivo webhook signature."""
        settings = get_settings()
        try:
            return plivo.utils.validate_signature(
                url=url,
                params=params,
                signature=signature,
                auth_token=settings.plivo_auth_token,
            )
        except Exception:
            return False

    # transfer_call removed — was scaffolded with wrong SDK signature.
    # TODO Sprint 3: implement using plivo.RestClient.calls.update with
    # aleg_url pointing to a Conference XML endpoint.


plivo_handler = PlivoHandler()
