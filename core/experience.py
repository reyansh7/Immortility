class Experience:
    def __init__(self, code, type):
        self.code = code
        self.type = type
        self.timestamp = datetime.datetime.now()

    def to_dict(self):
        return {
            'code': self.code,
            'type': self.type,
            'timestamp': self.timestamp.isoformat()
        }