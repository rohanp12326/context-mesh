import base64

def extract_body_from_payload(payload: dict) -> str:
    body_data = ""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        body_data = payload["body"]["data"]
    elif "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                body_data = part["body"]["data"]
                break
            elif "parts" in part:
                res = extract_body_from_payload(part)
                if res:
                    return res
        # Fallback to html
        if not body_data:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/html" and "data" in part.get("body", {}):
                    body_data = part["body"]["data"]
                    break

    if not body_data and "data" in payload.get("body", {}):
        body_data = payload["body"]["data"]

    if body_data:
        try:
            # Base64url decode
            decoded = base64.urlsafe_b64decode(body_data + "=" * (4 - len(body_data) % 4))
            return decoded.decode("utf-8", errors="replace")
        except Exception as e:
            return ""
    return ""
