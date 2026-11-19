"""Traduzione Anthropic Messages <-> OpenAI Chat Completions, per i fornitori che parlano solo OpenAI (Groq, Cloudflare, Gemini, Cerebras...).

La risposta del fornitore si legge SEMPRE per intero (senza streaming) e, se il client l'aveva chiesto in streaming, la si rimette in forma di
eventi SSE Anthropic: meno elegante ma molto piu' robusto della traduzione di flussi parziali, e per agenti che lavorano a turni non cambia nulla.
"""
from __future__ import annotations

import json
import uuid

STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "content_filter": "end_turn", "function_call": "tool_use"}


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def estimate_tokens(body: dict) -> int:
    return max(1, len(json.dumps(body, ensure_ascii=False, default=str)) // 4)


def to_openai(body: dict, model: str, max_tokens: int | None = None, tool_call_extra: dict | None = None) -> dict:
    msgs: list[dict] = []
    sysp = body.get("system")
    if sysp:
        msgs.append({"role": "system", "content": _text(sysp)})
    for m in body.get("messages", []):
        content = m["content"]
        if isinstance(content, str):
            msgs.append({"role": m["role"], "content": content})
            continue
        parts, calls, results = [], [], []
        for b in content:
            t = b.get("type")
            if t == "text":
                parts.append(b["text"])
            elif t == "tool_use":
                calls.append({"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}})
            elif t == "tool_result":
                txt = _text(b.get("content"))
                results.append((b["tool_use_id"], ("[errore] " + txt) if b.get("is_error") else txt))
        if m["role"] == "assistant":
            msg = {"role": "assistant", "content": "\n".join(parts)}          # mai null: Cloudflare, Mistral e Cerebras rifiutano content null anche con tool_calls
            if calls:
                if tool_call_extra:                                   # es. Gemini 3 chiede una thought_signature in ogni chiamata di strumento dei turni precedenti
                    calls = [{**c, **tool_call_extra} for c in calls]
                msg["tool_calls"] = calls
            msgs.append(msg)
        else:
            for tid, txt in results:                                   # i risultati degli strumenti seguono subito la chiamata
                msgs.append({"role": "tool", "tool_call_id": tid, "content": txt})
            if parts:
                msgs.append({"role": "user", "content": "\n".join(parts)})
    out = {"model": model, "messages": msgs, "max_tokens": min(int(body.get("max_tokens") or 4096), max_tokens or 10 ** 9)}
    for k in ("temperature", "top_p"):
        if body.get(k) is not None:
            out[k] = body[k]
    if body.get("tools"):
        out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": (t.get("description") or "")[:1000],
                                                          "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}} for t in body["tools"]]
        tc = body.get("tool_choice") or {}
        out["tool_choice"] = {"auto": "auto", "any": "required", "none": "none"}.get(tc.get("type"), "auto") if tc.get("type") != "tool" else \
            {"type": "function", "function": {"name": tc["name"]}}
    return out


def from_openai(r: dict, model: str) -> dict:
    choice = (r.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        content.append({"type": "tool_use", "id": tc.get("id") or "toolu_" + uuid.uuid4().hex[:12], "name": tc["function"]["name"], "input": args})
    u = r.get("usage") or {}
    return {"id": r.get("id") or "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant", "model": model,
            "content": content or [{"type": "text", "text": ""}],
            "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else STOP.get(choice.get("finish_reason"), "end_turn"),
            "stop_sequence": None, "usage": {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)}}


def to_sse(msg: dict) -> bytes:
    """Messaggio completo -> sequenza di eventi SSE Anthropic (message_start, blocchi, message_delta, message_stop)."""
    def ev(name: str, data: dict) -> bytes:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    out = [ev("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None,
                                                                        "usage": {"input_tokens": msg["usage"]["input_tokens"], "output_tokens": 0}}})]
    for i, b in enumerate(msg["content"]):
        if b["type"] == "text":
            out.append(ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}))
            out.append(ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}}))
        else:
            out.append(ev("content_block_start", {"type": "content_block_start", "index": i,
                                                  "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
            out.append(ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                                  "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"], ensure_ascii=False)}}))
        out.append(ev("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None},
                                    "usage": {"output_tokens": msg["usage"]["output_tokens"]}}))
    out.append(ev("message_stop", {"type": "message_stop"}))
    return b"".join(out)
