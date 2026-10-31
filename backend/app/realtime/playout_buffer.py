"""Bounded response quarantine. Unfinished or interrupted audio never escapes."""
class RealtimePlayoutBuffer:
    MAX_AUDIO_BYTES = 240_000  # 30 seconds of mono 8 kHz mu-law

    def __init__(self):
        self.reset()

    def reset(self, response_id=None):
        self.response_id = response_id
        self.audio = bytearray()
        self.parts = set()
        self.transcripts = {}
        self.invalid = False
        self.tools = []

    def owns(self, event):
        return bool(self.response_id and event.get("response_id") == self.response_id)

    @staticmethod
    def part(event):
        return (event.get("item_id"), event.get("content_index", 0))

    def add_audio(self, event, audio):
        if not self.owns(event) or self.invalid:
            return
        self.parts.add(self.part(event))
        if len(self.audio) + len(audio) > self.MAX_AUDIO_BYTES:
            self.audio.clear()
            self.invalid = True
        else:
            self.audio.extend(audio)

    def add_transcript(self, event):
        if self.owns(event):
            self.transcripts[self.part(event)] = str(event.get("transcript") or "").strip()

    def finish(self, response):
        if response.get("id") != self.response_id:
            return None
        ready = (response.get("status") == "completed" and not self.invalid
                 and self.parts and all(self.transcripts.get(p) for p in self.parts))
        result = (bytes(self.audio), " ".join(self.transcripts.values())) if ready else None
        return result
