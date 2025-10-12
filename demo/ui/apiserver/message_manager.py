class _MessageManager:
    def build_conversation_messages_from_memory(self, memory_messages, system_prompt, current_message, max_history_rounds=6):
        msgs = [{"role": "system", "content": system_prompt}]
        for m in (memory_messages or [])[-max_history_rounds:]:
            role = m.get("role", "user"); content = m.get("content", "")
            msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": current_message})
        return msgs
message_manager = _MessageManager()
