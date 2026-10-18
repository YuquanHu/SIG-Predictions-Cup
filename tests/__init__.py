import os

os.environ.setdefault("SIG_API_KEY", "test-key")  # config refuses to import without a key; tests never use it
